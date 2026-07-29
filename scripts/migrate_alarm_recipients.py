"""alarm_recipients / price_filter_keywords 생성 + 기존 카카오 토큰 이관 + 키워드 시드.

테이블 생성은 SQLAlchemy의 create_all(checkfirst=True)에 맡긴다. 프로덕션의 기존
테이블들은 pandas.to_sql로 만들어져 타입 드리프트가 있었지만(2026-07-28 H5,
2026-07-29 타입 드리프트 보정), create_all이 생성하는 DDL은 ORM 선언 그대로라
같은 문제가 생기지 않는다.

kakao_tokens의 단일 행(사장님 토큰)을 alarm_recipients 첫 행으로 옮긴다. 토큰
출처가 두 곳이면 갱신된 refresh_token이 한쪽에만 반영돼 다른 쪽이 조용히 죽는다.
kakao_tokens 테이블 자체는 롤백 대비로 남긴다.

멱등하다 — 여러 번 실행해도 중복 생성하지 않는다.

실행: python scripts/migrate_alarm_recipients.py
"""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select

from app.db.database import async_session, engine
from app.db.models import Base, AlarmRecipient, PriceFilterKeyword, KakaoToken

OWNER_NAME = "사장님"
SEED_KEYWORDS = [
    ("해외", "해외 판매/발송 표기"),
    ("구매대행", "구매대행 상품"),
    ("해외배송", "해외 직배송 상품"),
    ("직구", "해외 직구 상품"),
]


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[AlarmRecipient.__table__, PriceFilterKeyword.__table__],
            checkfirst=True,
        )
    print("[OK] 테이블 확인/생성 완료")

    async with async_session() as db:
        # ── 카카오 토큰 이관 ──
        existing = (await db.execute(
            select(AlarmRecipient).where(AlarmRecipient.name == OWNER_NAME)
        )).scalars().first()

        if existing:
            print(f"[SKIP] 수신자 '{OWNER_NAME}' 이미 존재 (id={existing.id})")
        else:
            token = (await db.execute(
                select(KakaoToken).where(KakaoToken.id == 1)
            )).scalars().first()
            if not token:
                print("[FAIL] kakao_tokens에 토큰이 없습니다. 최초 인증부터 하세요.")
                return
            db.add(AlarmRecipient(
                name=OWNER_NAME,
                channel="kakao",
                channel_token=token.refresh_token,
                access_token=token.access_token,
                token_expires_in=token.expires_in,
                token_obtained_at=token.obtained_at,
                is_active=True,
            ))
            await db.commit()
            print(f"[OK] 수신자 '{OWNER_NAME}' 이관 완료")

        # ── 키워드 시드 ──
        added = 0
        for keyword, note in SEED_KEYWORDS:
            found = (await db.execute(
                select(PriceFilterKeyword).where(PriceFilterKeyword.keyword == keyword)
            )).scalars().first()
            if found:
                continue
            db.add(PriceFilterKeyword(keyword=keyword, is_active=True, note=note))
            added += 1
        if added:
            await db.commit()
        print(f"[OK] 키워드 시드 완료 (신규 {added}건)")


if __name__ == "__main__":
    asyncio.run(main())
