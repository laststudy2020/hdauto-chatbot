"""응대 규칙 검증 — 3분기 응답, 내부정보 미노출, 알림 트리거 범위.

실행: python test_stock_reply_rules.py
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.db.database import async_session
from app.services import admin_notify
from app.services.inventory import get_inventory_status

UNKNOWN_MODEL = "존재하지않는모델XYZ999"
IN_STOCK_MODEL = "MR-J4-40A"

FORBIDDEN = ["원가", "마진", "타사 가격", "판매단가"]


async def main():
    ok = True
    calls = []
    orig = admin_notify.notify_admins

    async def spy(db, product, model_name, qty, state, force=False):
        calls.append((model_name, state))
        return {"sent": 0, "total": 0, "skipped": "test"}

    admin_notify.notify_admins = spy
    # inventory.py가 from-import로 잡아둔 참조까지 교체
    import app.services.inventory as inv_mod
    inv_mod.notify_admins = spy

    try:
        # ── 1) 미매칭 → "확인 후 안내", 알림 없음 ──
        calls.clear()
        async with async_session() as db:
            reply = await get_inventory_status(UNKNOWN_MODEL, db)
        print("── 미매칭 응답 ──")
        print(reply)
        if "확인 후 안내" in reply and "재고 없음" not in reply:
            print("[PASS] 미매칭은 재고를 단정하지 않고 확인 후 안내")
        else:
            print("[FAIL] 미매칭 응답이 규칙과 다름")
            ok = False
        if not calls:
            print("[PASS] 미매칭은 관리자 알림을 보내지 않음")
        else:
            print(f"[FAIL] 미매칭인데 알림 호출됨: {calls}")
            ok = False

        # ── 2) 재고 있음 → 알림 발생 ──
        calls.clear()
        async with async_session() as db:
            reply2 = await get_inventory_status(IN_STOCK_MODEL, db)
        print("\n── 재고 있음 응답 ──")
        print(reply2)
        if calls and calls[0][1] in ("in_stock", "low_stock"):
            print(f"[PASS] 재고 있어도 관리자 알림 호출됨 ({calls[0][1]})")
        else:
            print(f"[FAIL] 재고 있음인데 알림 호출 안 됨: {calls}")
            ok = False

        # ── 3) 내부 정보 미노출 ──
        leaked = [w for w in FORBIDDEN if w in reply or w in reply2]
        if not leaked:
            print("[PASS] 고객 응답에 내부 정보 없음")
        else:
            print(f"[FAIL] 내부 정보 노출: {leaked}")
            ok = False
    finally:
        admin_notify.notify_admins = orig
        inv_mod.notify_admins = orig

    print("\n결과:", "통과" if ok else "실패")


if __name__ == "__main__":
    asyncio.run(main())
