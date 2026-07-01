"""
네이버 커머스API 클라이언트 — 실시간 재고 조회 전용.

주의: app.config.Settings의 NAVER_CLIENT_ID/SECRET(네이버 검색 오픈API, 웹검색용)과는
완전히 별개의 API/인증체계입니다. 혼동하지 말 것.

인증 방식: OAuth2 Client Credentials Grant + bcrypt 서명
참고 문서: https://apicenter.commerce.naver.com/docs/commerce-api/current

──────────────────────────────────────────────
월요일 활성화 체크리스트
──────────────────────────────────────────────
1. .env에 NAVER_COMMERCE_CLIENT_ID / NAVER_COMMERCE_CLIENT_SECRET 채우기
2. 커머스API센터(apicenter.commerce.naver.com)에서 애플리케이션 API호출 IP
   등록 완료 (NAS 프록시 또는 Render Dedicated IP 등 확정된 IP)
3. Product.smartstore_product_id 컬럼 값이 "원상품번호(originProductNo)"인지
   확인할 것. 스마트스토어 URL에 보이는 번호는 "채널상품번호"라 다른 번호체계임.
   다르면 "상품목록조회" API로 원상품번호를 다시 매핑해야 함.
4. settings.NAVER_COMMERCE_ENABLED = True 로 전환 (.env 또는 Render 환경변수)
5. 실제 상품 1~2개로 get_live_stock_quantity() 단독 테스트 후 배포
──────────────────────────────────────────────
"""

import base64
import logging
import time
from datetime import datetime, timedelta

import bcrypt
import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

TOKEN_URL = "https://api.commerce.naver.com/external/v1/oauth2/token"
ORIGIN_PRODUCT_URL = "https://api.commerce.naver.com/external/v2/products/origin-products/{origin_product_no}"

# 프로세스 내 메모리 캐시 (서버 재시작 시 초기화됨 — 별도 영속 캐시 불필요한 수준)
_token_cache: dict = {"access_token": None, "expires_at": datetime.min}


class NaverCommerceError(Exception):
    """네이버 커머스API 호출 관련 오류 (호출 측에서 DB 폴백 트리거용)"""


def _build_signature(client_id: str, client_secret: str, timestamp: int) -> str:
    """`client_id_timestamp` 문자열을 client_secret으로 bcrypt 해싱 후 base64 인코딩."""
    password = f"{client_id}_{timestamp}".encode("utf-8")
    hashed = bcrypt.hashpw(password, client_secret.encode("utf-8"))
    return base64.b64encode(hashed).decode("utf-8")


async def _get_access_token() -> str:
    """Access Token 발급/캐싱. 만료 1분 전이면 미리 재발급."""
    now = datetime.utcnow()
    if _token_cache["access_token"] and now < _token_cache["expires_at"] - timedelta(minutes=1):
        return _token_cache["access_token"]

    client_id = settings.NAVER_COMMERCE_CLIENT_ID
    client_secret = settings.NAVER_COMMERCE_CLIENT_SECRET
    if not client_id or not client_secret:
        raise NaverCommerceError("NAVER_COMMERCE_CLIENT_ID/SECRET이 설정되지 않았습니다.")

    timestamp = int(time.time() * 1000)
    signature = _build_signature(client_id, client_secret, timestamp)

    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "client_id": client_id,
                    "timestamp": timestamp,
                    "client_secret_sign": signature,
                    "grant_type": "client_credentials",
                    "type": "SELF",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        # 실패 시 네이버가 보낸 실제 에러 메시지를 함께 노출 (디버깅용)
        detail = ""
        if e.response is not None:
            try:
                detail = f" | 응답내용: {e.response.text}"
            except Exception:
                pass
        raise NaverCommerceError(f"토큰 발급 실패: {e}{detail}") from e

    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = now + timedelta(seconds=int(data.get("expires_in", 3000)))
    logger.info("네이버 커머스API 토큰 발급/갱신 완료")
    return _token_cache["access_token"]


async def get_live_stock_quantity(origin_product_no: str) -> int:
    """원상품번호로 네이버 커머스API에서 실시간 재고수량(stockQuantity)을 조회.

    401(토큰 만료)이면 토큰 1회 재발급 후 재시도.
    429(레이트리밋) 포함, 그 외 모든 실패는 NaverCommerceError로 묶어서 올림 —
    호출 측(inventory.py)에서 이 예외를 잡아 DB 값으로 폴백하도록 설계됨.
    """
    url = ORIGIN_PRODUCT_URL.format(origin_product_no=origin_product_no)

    try:
        token = await _get_access_token()
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})

            if resp.status_code == 401:
                # 토큰 만료로 추정 — 캐시 비우고 1회만 재시도
                _token_cache["access_token"] = None
                token = await _get_access_token()
                resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})

        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        raise NaverCommerceError(f"상품 조회 실패 (origin_product_no={origin_product_no}): {e}") from e

    stock = data.get("originProduct", {}).get("stockQuantity")
    if stock is None:
        raise NaverCommerceError(f"응답에 stockQuantity 없음 (origin_product_no={origin_product_no})")

    return int(stock)
