"""다중 수신자 발송 + 실패 격리 검증.

1단계: 발송 없이 수신자 목록/메시지 조립까지 확인 (dry)
2단계: 실제 발송 1회
3단계: 가짜 수신자(잘못된 토큰)를 끼워 넣어 다른 수신자와 고객 응답이 온전한지 확인

실행: python test_multi_recipient_notify.py
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from app.db.database import async_session
from app.db.models import AlarmRecipient, Product
from app.services import admin_notify
from app.services.admin_notify import notify_admins, get_kakao_notify_health
from app.services.inventory import get_inventory_status

MODEL = "MR-J4-70A"
FAKE_NAME = "__테스트_깨진토큰__"


async def main():
    ok = True

    # ── 1) 수신자 목록 + 메시지 조립 (발송 없이) ──
    sent_texts = []
    orig_send = admin_notify._send_kakao_text

    async def spy_no_send(db, recipient, text):
        sent_texts.append((recipient.name, text))
        return True

    admin_notify._send_kakao_text = spy_no_send

    async with async_session() as db:
        recipients = (await db.execute(
            select(AlarmRecipient).where(
                AlarmRecipient.is_active.is_(True), AlarmRecipient.channel == "kakao"
            )
        )).scalars().all()
        print(f"활성 카카오 수신자 {len(recipients)}명: {[r.name for r in recipients]}")

        product = (await db.execute(
            select(Product).where(Product.model_name == MODEL)
        )).scalars().first()

        result = await notify_admins(db, product, MODEL, 2, "low_stock", force=True)
        print(f"발송 결과(모의): {result}")

    if len(sent_texts) == len(recipients) and len(recipients) >= 1:
        print(f"[PASS] 수신자 {len(recipients)}명 전원에게 조립됨")
    else:
        print(f"[FAIL] 조립 {len(sent_texts)}건 vs 수신자 {len(recipients)}명")
        ok = False

    if sent_texts:
        print("\n── 조립된 메시지 ──")
        print(sent_texts[0][1])

    admin_notify._send_kakao_text = orig_send

    # ── 2) 실제 발송 1회 ──
    async with async_session() as db:
        product = (await db.execute(
            select(Product).where(Product.model_name == MODEL)
        )).scalars().first()
        real = await notify_admins(db, product, MODEL, 2, "low_stock", force=True)
        print(f"\n실제 발송: {real}")
    if real["sent"] >= 1:
        print("[PASS] 실제 카카오 발송 성공")
    else:
        print("[FAIL] 실제 발송 0건 — 카톡 확인 필요")
        ok = False

    # ── 3) 실패 격리: 토큰이 깨진 가짜 수신자를 끼워 넣는다 ──
    async with async_session() as db:
        db.add(AlarmRecipient(
            name=FAKE_NAME,
            channel="kakao",
            channel_token="invalid-refresh-token",
            is_active=True,
        ))
        await db.commit()

    try:
        async with async_session() as db:
            product = (await db.execute(
                select(Product).where(Product.model_name == MODEL)
            )).scalars().first()
            mixed = await notify_admins(db, product, MODEL, 2, "low_stock", force=True)
            print(f"\n깨진 수신자 포함 발송: {mixed}")

        if mixed["sent"] >= 1 and mixed["sent"] < mixed["total"]:
            print("[PASS] 한 명 실패해도 나머지는 발송됨")
        else:
            print("[FAIL] 실패 격리가 동작하지 않음")
            ok = False

        health = get_kakao_notify_health()
        print(f"헬스 상태: {health}")
        if health["status"] == "failing" and any(
            r["name"] == FAKE_NAME for r in health.get("recipients", [])
        ):
            print("[PASS] 실패한 수신자를 이름으로 식별 가능")
        else:
            print("[FAIL] 헬스 상태에 실패 수신자가 안 잡힘")
            ok = False

        # 고객 응답이 온전한지
        async with async_session() as db:
            reply = await get_inventory_status(MODEL, db)
        if "재고" in reply and "오류" not in reply:
            print("[PASS] 발송 실패가 있어도 고객 응답 정상")
        else:
            print(f"[FAIL] 고객 응답이 손상됨: {reply[:80]}")
            ok = False

    finally:
        # 가짜 수신자 정리
        async with async_session() as db:
            fake = (await db.execute(
                select(AlarmRecipient).where(AlarmRecipient.name == FAKE_NAME)
            )).scalars().first()
            if fake:
                await db.delete(fake)
                await db.commit()
                print("\n가짜 수신자 정리 완료")

    # ── 4) 삭제된 수신자의 실패 기록이 정리되는지 ──
    # 남아 있으면 헬스 상태가 영구히 "failing"으로 보여 진짜 장애를 놓친다.
    admin_notify._send_kakao_text = spy_no_send
    async with async_session() as db:
        await notify_admins(db, None, MODEL, 2, "low_stock", force=True)
    admin_notify._send_kakao_text = orig_send

    health_after = get_kakao_notify_health()
    print(f"정리 후 헬스 상태: {health_after}")
    if health_after["status"] == "ok":
        print("[PASS] 삭제된 수신자의 실패 기록이 정리됨")
    else:
        print("[FAIL] 존재하지 않는 수신자의 실패 기록이 남아 있음")
        ok = False

    print("\n결과:", "통과" if ok else "실패")


if __name__ == "__main__":
    asyncio.run(main())
