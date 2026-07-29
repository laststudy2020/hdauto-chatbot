"""해외 필터와 메시지 포맷 검증 — 카카오 발송 없이 조립까지만 확인.

실행: python test_price_filter.py
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.db.database import async_session
from app.services.admin_notify import (
    _load_filter_keywords, _get_competitor_prices, _build_message,
)

FAKE_ITEMS = [
    {"title": "해외 미쓰비시 MR-J4-70A 서보앰프", "mall": "직구몰", "price": 90000},
    {"title": "미쓰비시 MR-J4-70A 서보앰프", "mall": "국내스토어", "price": 410000},
    {"title": "MR-J4-70A 구매대행", "mall": "무역상사", "price": 88000},
]


def _apply(keywords, items):
    """_get_competitor_prices의 필터 판정과 같은 규칙을 검증용으로 재현."""
    kept, excluded = [], 0
    for it in items:
        if any(kw in f"{it['title']} {it['mall']}" for kw in keywords):
            excluded += 1
            continue
        kept.append(it)
    return kept, excluded


async def main():
    ok = True
    async with async_session() as db:
        keywords = await _load_filter_keywords(db)
        print(f"활성 키워드: {keywords}")

        kept, excluded = _apply(keywords, FAKE_ITEMS)
        print(f"가짜 데이터 3건 → 유지 {len(kept)}건 / 제외 {excluded}건")
        if len(kept) == 1 and excluded == 2:
            print("[PASS] 해외/구매대행 2건 제외, 국내 1건 유지")
        else:
            print("[FAIL] 필터 판정이 기대와 다름")
            ok = False

        # 일부 제외 메시지
        msg = _build_message(
            "MR-J4-70A", 2, "low_stock", 420000,
            [{"title": "미쓰비시 MR-J4-70A", "mall": "국내스토어", "price": 410000}],
            excluded_count=2,
        )
        print("\n── 일부 제외 ──")
        print(msg)
        if "※ 해외 표기 상품 2건은 비교 대상에서 제외됨" in msg and "조회 시각:" in msg:
            print("[PASS] 제외 표기 + 조회 시각 포함")
        else:
            print("[FAIL] 제외 표기 또는 조회 시각 누락")
            ok = False

        # 전부 제외 메시지
        msg_all = _build_message("MR-J4-70A", 2, "low_stock", 420000, [], excluded_count=3)
        print("\n── 전부 제외 ──")
        print(msg_all)
        if "경쟁사 단가: 해외 표기 상품으로 제외됨" in msg_all:
            print("[PASS] 전부 제외 문구")
        else:
            print("[FAIL] 전부 제외 문구 없음")
            ok = False

        # 검색 결과 자체가 없음
        msg_none = _build_message("존재하지않는모델", 0, "out_of_stock", None, [], excluded_count=0)
        if "타사 가격 검색 결과 없음" in msg_none:
            print("[PASS] 검색 결과 없음 문구 유지")
        else:
            print("[FAIL] 검색 결과 없음 문구가 바뀜")
            ok = False

        # 실제 API 1회 호출 (네트워크 확인용, 실패해도 위 판정과 무관)
        try:
            real, real_excluded = await _get_competitor_prices("MR-J4-70A", keywords)
            print(f"\n실제 네이버쇼핑 조회: 유지 {len(real)}건 / 제외 {real_excluded}건")
            for c in real:
                print(f"  · [{c['mall']}] {c['price']:,}원 - {c['title'][:40]}")
        except Exception as e:
            print(f"\n(실제 API 조회 실패 — 판정에는 영향 없음: {e})")

    print("\n결과:", "통과" if ok else "실패")


if __name__ == "__main__":
    asyncio.run(main())
