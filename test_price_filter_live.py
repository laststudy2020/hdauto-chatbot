"""실제 네이버쇼핑 데이터에서 해외/직구 제외가 걸리는지 진단.

가짜 데이터 검증(test_price_filter_exclusion.py)은 '판정 코드가 맞는가'를 보고,
이 스크립트는 '키워드가 현실의 상품명/쇼핑몰명과 실제로 맞아떨어지는가'를 본다.
읽기 전용 — 발송도 DB 기록도 하지 않는다.

실행: python test_price_filter_live.py
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

import httpx

from app.config import get_settings
from app.db.database import async_session
from app.services.admin_notify import (
    NAVER_SHOP_URL, MY_MALL_KEYWORDS, _load_filter_keywords,
)

settings = get_settings()

MODELS = ["MR-J4-70A", "IG5A", "FR-E700", "MR-J2S-40A"]


async def fetch(model: str) -> list[dict]:
    async with httpx.AsyncClient(trust_env=False, timeout=10.0) as client:
        resp = await client.get(
            NAVER_SHOP_URL,
            params={"query": model, "display": 20, "sort": "asc"},
            headers={
                "X-Naver-Client-Id": settings.NAVER_SHOPPING_CLIENT_ID or settings.NAVER_CLIENT_ID,
                "X-Naver-Client-Secret": settings.NAVER_SHOPPING_CLIENT_SECRET or settings.NAVER_CLIENT_SECRET,
            },
        )
    if not resp.is_success:
        print(f"  (검색 실패 {resp.status_code}: {resp.text[:120]})")
        return []
    return resp.json().get("items", [])


async def main():
    async with async_session() as db:
        keywords = await _load_filter_keywords(db)
    print(f"활성 제외 키워드: {keywords}\n")

    total_excluded = 0
    for model in MODELS:
        print(f"═══ {model} ═══")
        items = await fetch(model)
        if not items:
            print("  검색 결과 없음\n")
            continue

        kept = excluded = mine = 0
        for item in items:
            mall = item["mallName"]
            title = item["title"].replace("<b>", "").replace("</b>", "")
            price = int(item["lprice"])

            if any(kw in mall for kw in MY_MALL_KEYWORDS):
                mine += 1
                print(f"  [자사] [{mall}] {price:,}원 - {title[:45]}")
                continue

            hit = [kw for kw in keywords if kw in f"{title} {mall}"]
            if hit:
                excluded += 1
                print(f"  [제외:{','.join(hit)}] [{mall}] {price:,}원 - {title[:45]}")
            else:
                kept += 1
                if kept <= 3:
                    print(f"  [유지] [{mall}] {price:,}원 - {title[:45]}")

        total_excluded += excluded
        print(f"  → 총 {len(items)}건 중 유지 {kept} / 제외 {excluded} / 자사 {mine}\n")

    print("─────────────")
    if total_excluded:
        print(f"[PASS] 실제 데이터에서 제외 판정이 총 {total_excluded}건 발생 — 키워드가 현실과 맞음")
    else:
        print("[WARN] 실제 데이터에서 제외가 한 건도 없었음. 필터가 고장났을 수도 있고,")
        print("       검색된 상품에 해외 표기가 없었을 수도 있다 — 위 목록을 눈으로 확인할 것.")


if __name__ == "__main__":
    asyncio.run(main())
