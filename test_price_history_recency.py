"""경쟁사 단가 조회가 프로덕션에서 언제까지 살아 있었는지 확인.

_get_competitor_prices()가 빈 결과를 돌려주면 PriceHistory 행이 생기지 않는다
(notify_admins는 competitors와 our_price가 모두 있을 때만 기록한다).
따라서 StockAlert는 계속 쌓이는데 PriceHistory만 끊긴 시점이 곧 쇼핑 검색이
죽은 시점이다. 읽기 전용.

실행: python test_price_history_recency.py
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select, desc

from app.db.database import async_session
from app.db.models import PriceHistory, StockAlert


async def main():
    async with async_session() as db:
        alerts = (await db.execute(
            select(StockAlert).order_by(desc(StockAlert.id)).limit(10)
        )).scalars().all()
        print(f"── 최근 재고 알림 {len(alerts)}건 ──")
        for a in alerts:
            print(f"  #{a.id} product={a.product_id} {a.alert_type} sent_at={a.sent_at}")

        prices = (await db.execute(
            select(PriceHistory).order_by(desc(PriceHistory.id)).limit(10)
        )).scalars().all()
        print(f"\n── 최근 경쟁사 단가 기록 {len(prices)}건 ──")
        for p in prices:
            print(f"  #{p.id} product={p.product_id} 자사={p.our_price} "
                  f"최저={p.competitor_min} 건수={p.competitor_count} checked_at={p.checked_at}")

        print("\n── 판정 ──")
        if not prices:
            print("PriceHistory가 아예 비어 있음 — 경쟁사 조회가 한 번도 기록되지 않았다.")
        elif alerts:
            last_alert = alerts[0].sent_at
            last_price = prices[0].checked_at
            print(f"마지막 재고 알림 : {last_alert}")
            print(f"마지막 단가 기록 : {last_price}")
            if last_alert and last_price and last_alert > last_price:
                print("→ 알림은 계속 나가는데 단가 기록만 끊겼다. 쇼핑 검색 실패가 유력하다.")
            else:
                print("→ 최근 알림에도 단가가 함께 기록되고 있다.")


if __name__ == "__main__":
    asyncio.run(main())
