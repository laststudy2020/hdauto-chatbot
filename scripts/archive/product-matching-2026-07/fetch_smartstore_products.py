"""
1단계: 스마트스토어 전체 상품 목록을 네이버 커머스API로 가져와서 로컬 JSON에 저장.

상품목록조회 API(POST /v1/products/search) 한 번으로 originProductNo,
channelProductNo, 상품명(name), 재고(stockQuantity), 판매상태가 함께 응답되므로
채널상품조회를 개별 호출할 필요가 없다 (기존 마이그레이션 계획 대비 API 호출 대폭 절감).

이 스크립트는 DB를 건드리지 않는다. 목적은:
  1. 3000여 건 전체 상품을 한 번 받아서 로컬에 캐싱 (재실행 시 API 재호출 방지)
  2. 상품명에 모델명이 어떤 패턴으로 들어있는지 샘플을 눈으로 확인하기 위함
     (2단계 매칭 스크립트를 짜기 전 필수 확인 단계)

실행:
  python fetch_smartstore_products.py
  → products_raw.json 생성 + 상품명 샘플 30개 출력
"""
import asyncio
import json
import time

import httpx

from app.services.naver_commerce import _get_access_token, NaverCommerceError

SEARCH_URL = "https://api.commerce.naver.com/external/v1/products/search"
PAGE_SIZE = 100  # 최대 100 (API 제한 확인 필요 시 조정)
OUTPUT_FILE = "products_raw.json"


async def fetch_page(client: httpx.AsyncClient, token: str, page: int) -> dict:
    body = {
        "productStatusTypes": ["SALE"],  # 판매중 상태만
        "page": page,
        "size": PAGE_SIZE,
        "orderType": "NO",
    }
    resp = await client.post(
        SEARCH_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
    )
    if resp.status_code == 401:
        token = await _get_access_token()
        resp = await client.post(
            SEARCH_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
        )
    if resp.status_code >= 400:
        print(f"\n  [네이버 응답 본문] status={resp.status_code}\n  {resp.text}\n")
    resp.raise_for_status()
    return resp.json()


async def fetch_all_products() -> list[dict]:
    token = await _get_access_token()
    all_items: list[dict] = []
    page = 1

    async with httpx.AsyncClient(timeout=20.0) as client:
        while True:
            print(f"  페이지 {page} 조회 중...")
            try:
                data = await fetch_page(client, token, page)
            except httpx.HTTPError as e:
                raise NaverCommerceError(f"상품목록조회 실패 (page={page}): {e}") from e

            contents = data.get("contents", [])
            if not contents:
                break

            for item in contents:
                origin_no = item.get("originProductNo")
                for cp in item.get("channelProducts", []):
                    all_items.append({
                        "origin_product_no": origin_no,
                        "channel_product_no": cp.get("channelProductNo"),
                        "name": cp.get("name"),
                        "seller_management_code": cp.get("sellerManagementCode"),
                        "status_type": cp.get("statusType"),
                        "stock_quantity": cp.get("stockQuantity"),
                    })

            total_pages = data.get("totalPages")
            print(f"    누적 {len(all_items)}건 (총 {total_pages}페이지 중 {page})")

            if total_pages and page >= total_pages:
                break
            if len(contents) < PAGE_SIZE:
                break

            page += 1
            await asyncio.sleep(0.3)  # 레이트리밋 여유

    return all_items


def print_sample(items: list[dict], n: int = 30):
    print("\n" + "=" * 60)
    print(f"상품명 샘플 (최대 {n}건) — 모델명이 어떻게 들어있는지 확인용")
    print("=" * 60)
    for item in items[:n]:
        print(
            f"  채널상품번호={item['channel_product_no']} | "
            f"원상품번호={item['origin_product_no']} | "
            f"상품명='{item['name']}' | "
            f"관리코드='{item['seller_management_code']}'"
        )


async def main():
    print("스마트스토어 전체 상품 조회 시작 (판매중 상태)\n")
    items = await fetch_all_products()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"\n총 {len(items)}건 저장 완료 -> {OUTPUT_FILE}")
    print_sample(items)

    print(
        "\n다음 단계: 위 상품명 샘플을 보고 모델명이 어떤 형태로 들어있는지 확인해주세요."
        "\n(예: 상품명에 'MR-J4-70A'가 그대로 포함되는지, 띄어쓰기/대소문자 차이가 있는지,"
        "\n 혹은 sellerManagementCode에 모델명이 별도로 들어있는지 등)"
        "\n확인되면 그 패턴에 맞춰 2단계 매칭 스크립트를 작성합니다."
    )


if __name__ == "__main__":
    asyncio.run(main())