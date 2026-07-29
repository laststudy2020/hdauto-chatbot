"""alarm_recipients / price_filter_keywords 테이블과 초기 데이터 검증.

실행: python test_alarm_recipients.py
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from app.db.database import async_session
from app.db.models import AlarmRecipient, PriceFilterKeyword

EXPECTED_KEYWORDS = {"해외", "구매대행", "해외배송", "직구"}


async def main():
    ok = True
    async with async_session() as db:
        recipients = (await db.execute(select(AlarmRecipient))).scalars().all()
        print(f"수신자 {len(recipients)}명")
        for r in recipients:
            has_token = bool(r.channel_token)
            print(f"  #{r.id} {r.name} channel={r.channel} active={r.is_active} "
                  f"refresh_token={'있음' if has_token else '없음'}")

        active_kakao = [r for r in recipients if r.is_active and r.channel == "kakao"]
        if len(active_kakao) >= 1 and all(r.channel_token for r in active_kakao):
            print("[PASS] 활성 카카오 수신자 1명 이상, 전원 refresh_token 보유")
        else:
            print("[FAIL] 활성 카카오 수신자가 없거나 토큰이 비어 있음")
            ok = False

        keywords = (await db.execute(select(PriceFilterKeyword))).scalars().all()
        found = {k.keyword for k in keywords if k.is_active}
        print(f"\n활성 필터 키워드: {sorted(found)}")
        if EXPECTED_KEYWORDS <= found:
            print("[PASS] 해외 계열 키워드 4종 모두 존재")
        else:
            print(f"[FAIL] 누락: {sorted(EXPECTED_KEYWORDS - found)}")
            ok = False

    print("\n결과:", "통과" if ok else "실패")


if __name__ == "__main__":
    asyncio.run(main())
