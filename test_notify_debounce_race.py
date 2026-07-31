"""디바운스 경쟁 구간 검증 — 같은 모델을 동시에 물어봐도 알림은 1회만 나가야 한다.

notify_admins()는 디바운스 검사와 기록 사이에 await가 여러 번 있다(수신자 조회,
타사가격 조회, 카카오 발송, 커밋). 예전에는 기록이 발송 '뒤'에 있어서 동시 요청이
둘 다 검사를 통과해 같은 알림이 중복 발송됐다. 지금은 슬롯을 발송 전에 선점한다.

DB/카카오/네이버를 전혀 건드리지 않는다 — 발송 함수와 외부 조회를 가짜로 바꾸고,
느린 응답을 흉내내 경쟁 구간을 실제로 벌린다. 그래서 실제 카톡은 발송되지 않는다.

실행: python test_notify_debounce_race.py
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.services import admin_notify
from app.services.admin_notify import notify_admins

MODEL = "__디바운스테스트__MR-J4-70A"

sends: list[str] = []
results: list[tuple[bool, str]] = []


def check(ok, label, detail=""):
    results.append((bool(ok), label))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    if detail:
        for line in str(detail).splitlines():
            print(f"       {line}")


class FakeRecipient:
    id = 9001
    name = "가짜수신자"
    channel = "kakao"
    is_active = True
    channel_token = "fake"


class FakeDB:
    """notify_admins()가 쓰는 최소 인터페이스만 흉내낸다."""

    async def execute(self, *a, **kw):
        await asyncio.sleep(0.05)  # 경쟁 구간을 벌리는 await

        class R:
            def scalars(self):
                class S:
                    def all(self):
                        return [FakeRecipient()]
                return S()
        return R()

    def add(self, *a, **kw):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass


async def fake_send(db, recipient, text):
    await asyncio.sleep(0.1)  # 카카오 API 왕복을 흉내
    sends.append(recipient.name)
    return True


async def fake_send_fail(db, recipient, text):
    await asyncio.sleep(0.1)
    return False


async def fake_keywords(db):
    return []


async def fake_prices(model_name, keywords):
    await asyncio.sleep(0.05)
    return [], 0


def reset():
    sends.clear()
    admin_notify._last_notified.pop(MODEL, None)


async def main():
    admin_notify._send_kakao_text = fake_send
    admin_notify._load_filter_keywords = fake_keywords
    admin_notify._get_competitor_prices = fake_prices

    # ── 1) 동시 호출 3건 → 발송은 1회만 ──
    reset()
    out = await asyncio.gather(*[
        notify_admins(FakeDB(), None, MODEL, 2, "low_stock") for _ in range(3)
    ])
    sent_total = sum(r["sent"] for r in out)
    debounced = sum(1 for r in out if r["skipped"] == "debounce")
    check(len(sends) == 1, f"동시 3건 → 실제 발송 1회 (실제 {len(sends)}회)",
          "" if len(sends) == 1 else f"발송 기록: {sends}\n반환값: {out}")
    check(sent_total == 1, f"sent 합계 1 (실제 {sent_total})")
    check(debounced == 2, f"나머지 2건은 debounce로 스킵 (실제 {debounced})")

    # ── 2) 순차 재호출도 여전히 막힌다 (기존 동작 회귀 확인) ──
    r = await notify_admins(FakeDB(), None, MODEL, 2, "low_stock")
    check(r["skipped"] == "debounce", f"디바운스 유지 중 재호출 스킵 (실제 {r['skipped']})")

    # ── 3) force=True는 디바운스를 무시한다 ──
    reset_count = len(sends)
    r = await notify_admins(FakeDB(), None, MODEL, 2, "low_stock", force=True)
    check(r["sent"] == 1 and len(sends) == reset_count + 1,
          f"force=True는 디바운스 무시하고 발송 (sent={r['sent']})")

    # ── 4) 발송이 전부 실패하면 슬롯을 되돌려, 다음 시도가 막히지 않는다 ──
    reset()
    admin_notify._send_kakao_text = fake_send_fail
    r1 = await notify_admins(FakeDB(), None, MODEL, 2, "low_stock")
    check(r1["sent"] == 0, f"발송 실패 시 sent=0 (실제 {r1['sent']})")
    check(MODEL not in admin_notify._last_notified,
          "발송 전부 실패 → 디바운스 슬롯 해제됨",
          "" if MODEL not in admin_notify._last_notified else
          "실패한 시도가 한 시간 동안 재알림을 막습니다.")

    admin_notify._send_kakao_text = fake_send
    r2 = await notify_admins(FakeDB(), None, MODEL, 2, "low_stock")
    check(r2["sent"] == 1, f"실패 직후 재시도는 발송됨 (실제 sent={r2['sent']})")

    # ── 5) 수신자가 없어도 슬롯이 남지 않는다 ──
    reset()

    class EmptyDB(FakeDB):
        async def execute(self, *a, **kw):
            await asyncio.sleep(0.05)

            class R:
                def scalars(self):
                    class S:
                        def all(self):
                            return []
                    return S()
            return R()

    r = await notify_admins(EmptyDB(), None, MODEL, 2, "low_stock")
    check(r["skipped"] == "no_recipients", f"수신자 0명 → no_recipients (실제 {r['skipped']})")
    check(MODEL not in admin_notify._last_notified,
          "수신자 0명일 때 디바운스 슬롯 해제됨")

    reset()
    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
    if failed:
        print("실패 항목:")
        for label in failed:
            print(f"  · {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
