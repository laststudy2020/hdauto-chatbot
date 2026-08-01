"""네이버쇼핑 검색 SE05 원인 좁히기.

경쟁사 단가 조회가 404 SE05로 실패한다. 자격증명 문제인지, 엔드포인트 문제인지,
값에 공백/따옴표가 섞인 문제인지 가른다. 시크릿은 출력하지 않는다.

실행: python test_naver_shop_api_diag.py
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

import httpx

from app.config import get_settings

settings = get_settings()

ENDPOINTS = [
    ("shop", "https://openapi.naver.com/v1/search/shop.json"),
    ("news", "https://openapi.naver.com/v1/search/news.json"),
]

PAIRS = [
    ("NAVER_SHOPPING_*", settings.NAVER_SHOPPING_CLIENT_ID, settings.NAVER_SHOPPING_CLIENT_SECRET),
    ("NAVER_* (폴백)", settings.NAVER_CLIENT_ID, settings.NAVER_CLIENT_SECRET),
]


def mask(value: str | None) -> str:
    if not value:
        return "(빈 값)"
    return f"{value[:6]}… (길이 {len(value)}, repr {value[:3]!r}…)"


async def probe(name: str, url: str, client_id: str, secret: str) -> None:
    async with httpx.AsyncClient(trust_env=False, timeout=10.0) as client:
        resp = await client.get(
            url,
            params={"query": "MR-J4-70A", "display": 3},
            headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": secret},
        )
    if resp.is_success:
        count = len(resp.json().get("items", []))
        print(f"    {name}: OK ({count}건)")
    else:
        print(f"    {name}: {resp.status_code} {resp.text.strip()[:150]}")


async def main():
    for label, cid, sec in PAIRS:
        print(f"\n═══ {label} ═══")
        print(f"  client_id: {mask(cid)}")
        print(f"  secret   : {mask(sec)}")
        if not cid or not sec:
            print("  → 값이 비어 있어 호출 생략")
            continue
        for name, url in ENDPOINTS:
            await probe(name, url, cid, sec)


if __name__ == "__main__":
    asyncio.run(main())
