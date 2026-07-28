"""
스마트스토어 채널상품번호 → 원상품번호(originProductNo) 매핑 마이그레이션.

배경:
  Product.smartstore_product_id 컬럼에 입력된 값은 스마트스토어 URL에서 그대로
  복사해온 값일 가능성이 높고, 이는 "채널상품번호"이지 네이버 커머스API의
  원상품 조회(GET /v2/products/origin-products/{originProductNo})가 요구하는
  "원상품번호"가 아니다. 두 번호 체계는 서로 다르며, 한 상품의 원상품번호가
  다른 상품의 채널상품번호와 우연히 같은 값일 수도 있어 혼용 시 위험하다.

동작:
  1. 채널 상품 조회 API(GET /v2/products/channel-products/{channelProductNo})로
     기존 smartstore_product_id(채널상품번호로 간주)를 조회
  2. 응답에서 원상품번호(originProductNo)를 추출
  3. Product.origin_product_no 컬럼에 저장 (기존 smartstore_product_id는 보존)
  4. 실패 항목은 건너뛰고 마지막에 요약 출력

실행 전 필수:
  - .env에 NAVER_COMMERCE_CLIENT_ID / NAVER_COMMERCE_CLIENT_SECRET 설정
  - 커머스API센터에 호출 IP 등록 완료 (이 스크립트를 실행하는 환경의 IP)
  - app/db/models.py의 Product 테이블에 origin_product_no 컬럼 추가 후
    DB 마이그레이션(또는 init_db) 선행

실행:
  python migrate_origin_product_no.py            # 실제 반영
  python migrate_origin_product_no.py --dry-run   # 조회만 하고 DB는 안 건드림
"""
import asyncio
import sys

from sqlalchemy import select

from app.db.database import async_session
from app.db.models import Product
from app.services.naver_commerce import (
    _get_access_token,
    NaverCommerceError,
)
import httpx
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

CHANNEL_PRODUCT_URL = "https://api.commerce.naver.com/external/v2/products/channel-products/{channel_product_no}"


class MigrationResult:
    def __init__(self):
        self.success: list[tuple[str, str, str]] = []   # model_name, channel_no, origin_no
        self.skipped: list[tuple[str, str]] = []          # model_name, reason
        self.failed: list[tuple[str, str, str]] = []      # model_name, channel_no, error


async def get_origin_product_no(channel_product_no: str) -> str:
    """채널상품번호로 채널 상품 조회 API를 호출해 원상품번호를 반환."""
    url = CHANNEL_PRODUCT_URL.format(channel_product_no=channel_product_no)
    token = await _get_access_token()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})

            if resp.status_code == 401:
                token = await _get_access_token()
                resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})

        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        raise NaverCommerceError(
            f"채널상품 조회 실패 (channel_product_no={channel_product_no}): {e}"
        ) from e

    origin_no = data.get("originProductNo")
    if not origin_no:
        raise NaverCommerceError(
            f"응답에 originProductNo 없음 (channel_product_no={channel_product_no})"
        )
    return str(origin_no)


async def migrate(dry_run: bool = False) -> MigrationResult:
    result = MigrationResult()

    async with async_session() as db:
        stmt = select(Product).where(
            Product.smartstore_product_id.isnot(None),
            Product.smartstore_product_id != "",
        )
        products = (await db.execute(stmt)).scalars().all()

        if not products:
            print("smartstore_product_id가 등록된 제품이 없습니다.")
            return result

        print(f"대상 제품 {len(products)}건 처리 시작{' (dry-run)' if dry_run else ''}\n")

        for product in products:
            channel_no = product.smartstore_product_id

            # 이미 origin_product_no가 채워져 있으면 건너뜀 (재실행 안전성)
            if getattr(product, "origin_product_no", None):
                result.skipped.append((product.model_name, "이미 origin_product_no 존재"))
                continue

            try:
                origin_no = await get_origin_product_no(channel_no)
            except NaverCommerceError as e:
                logger.warning(f"  ✗ {product.model_name} ({channel_no}): {e}")
                result.failed.append((product.model_name, channel_no, str(e)))
                continue

            print(f"  ✓ {product.model_name}: 채널상품번호 {channel_no} → 원상품번호 {origin_no}")
            result.success.append((product.model_name, channel_no, origin_no))

            if not dry_run:
                product.origin_product_no = origin_no

        if not dry_run and result.success:
            await db.commit()

    return result


def print_summary(result: MigrationResult, dry_run: bool):
    print("\n" + "=" * 50)
    print(f"마이그레이션 {'시뮬레이션' if dry_run else ''} 완료")
    print("=" * 50)
    print(f"성공: {len(result.success)}건")
    print(f"건너뜀(이미 처리됨): {len(result.skipped)}건")
    print(f"실패: {len(result.failed)}건")

    if result.failed:
        print("\n[실패 목록 — 수동 확인 필요]")
        for model_name, channel_no, error in result.failed:
            print(f"  - {model_name} (채널상품번호: {channel_no}) : {error}")

    if dry_run and result.success:
        print(
            "\ndry-run 모드라 DB에는 반영되지 않았습니다. "
            "위 결과가 맞으면 --dry-run 없이 다시 실행해 실제로 반영하세요."
        )


async def main():
    dry_run = "--dry-run" in sys.argv
    result = await migrate(dry_run=dry_run)
    print_summary(result, dry_run)


if __name__ == "__main__":
    asyncio.run(main())