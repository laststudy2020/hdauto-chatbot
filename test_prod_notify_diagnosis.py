"""프로덕션 알림 실패가 '토큰 갱신 단계'에서 났음을 서버 측만으로 확정한다.

## 원리

_send_kakao_text()는 access_token만 쓴다 — client_id/secret이 필요 없다. 반면
_get_valid_access_token()은 캐시가 만료됐을 때만 client_id/secret으로 갱신을 시도한다.

따라서 로컬에서 access_token을 미리 갱신해 DB에 넣어두면, 프로덕션은 갱신을 건너뛰고
캐시된 토큰으로 바로 발송한다. 이때 발송이 성공하면 실패 지점이 '갱신'이었음이 확정된다.

## 발송 성공 여부를 사람 확인 없이 판정하는 방법

notify_admins()는 sent > 0일 때만 _last_notified[model]을 설정한다. 따라서:

  1차 문의 → StockAlert 행 생성
  2차 문의(즉시)
    · 새 행이 없다  → 디바운스 작동 → 1차 발송 성공
    · 새 행이 생긴다 → 디바운스 미설정 → 1차 발송 실패

실행: python test_prod_notify_diagnosis.py
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")

import httpx
from sqlalchemy import select

from app.db.database import async_session
from app.db.models import AlarmRecipient, Product, StockAlert

BASE = "https://hdauto-chatbot.onrender.com"
KST = timezone(timedelta(hours=9))
MODEL = "MR-J4-40A"   # 아직 알림이 나가지 않은 모델을 쓴다

results: list[tuple[bool, str]] = []


def check(ok, label: str, detail: str = ""):
    results.append((bool(ok), label))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    if detail:
        for line in detail.splitlines():
            print(f"       {line}")


async def _max_alert_id() -> int:
    async with async_session() as db:
        return (await db.execute(
            select(StockAlert.id).order_by(StockAlert.id.desc()).limit(1)
        )).scalar() or 0


async def _recipient_token_state():
    async with async_session() as db:
        r = (await db.execute(
            select(AlarmRecipient).where(AlarmRecipient.id == 1)
        )).scalars().first()
        return (r.token_obtained_at, r.token_expires_in) if r else (None, None)


async def _ask(client: httpx.AsyncClient, model: str) -> str | None:
    try:
        resp = await client.post(
            f"{BASE}/api/chat/",
            json={"message": f"{model} 재고 있나요?", "user_id": "notify-diagnosis",
                  "channel": "webchat"},
        )
    except Exception as e:
        print(f"   요청 예외: {type(e).__name__} (서버는 계속 처리할 수 있음)")
        return None
    if resp.status_code != 200:
        print(f"   HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    return resp.json().get("reply", "")


async def main():
    obtained_before, expires_in = await _recipient_token_state()
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    age = (now_utc - obtained_before).total_seconds() if obtained_before else None
    print(f"수신자 #1 토큰 상태")
    print(f"  token_obtained_at = {obtained_before} (UTC 기준 {age:.0f}초 전)")
    print(f"  token_expires_in  = {expires_in}초")
    cache_valid = age is not None and expires_in and age < expires_in - 60
    check(cache_valid, "access_token 캐시가 유효 — 프로덕션은 갱신을 건너뛴다",
          "캐시가 만료돼 프로덕션이 갱신을 시도하게 됩니다. "
          "diagnose_kakao_send.py를 먼저 실행하세요." if not cache_valid else "")
    if not cache_valid:
        return

    base_id = await _max_alert_id()
    print(f"\n기준선: stock_alerts 최대 id={base_id}\n")

    async with httpx.AsyncClient(trust_env=False, timeout=240.0) as client:
        # ── 1차 문의 ──
        print(f"── 1차 문의: {MODEL}")
        reply1 = await _ask(client, MODEL)
        await asyncio.sleep(5)
        after1 = await _max_alert_id()
        new1 = after1 - base_id
        print(f"   새 StockAlert {new1}건 (id {base_id} → {after1})")
        check(new1 >= 1, f"1차 문의로 알림 경로 실행됨",
              "디바운스로 스킵됐거나 모델이 카탈로그에 없습니다." if new1 < 1 else "")
        if new1 < 1:
            return

        # ── 2차 문의 (디바운스 판정) ──
        print(f"\n── 2차 문의 (즉시): {MODEL}")
        reply2 = await _ask(client, MODEL)
        await asyncio.sleep(5)
        after2 = await _max_alert_id()
        new2 = after2 - after1
        print(f"   새 StockAlert {new2}건 (id {after1} → {after2})")

        sent_ok = new2 == 0
        check(sent_ok,
              "2차가 디바운스로 차단됨 → 1차 카카오 발송 성공",
              "" if sent_ok else
              "2차도 알림 기록이 생겼습니다 = 1차에서 sent=0 = 발송 실패.\n"
              "access_token이 유효한데도 실패했다면 갱신 단계가 아닌 발송 단계 문제입니다.")

    # ── 토큰이 갱신되지 않았는지 확인 (프로덕션이 캐시를 썼다는 증거) ──
    obtained_after, _ = await _recipient_token_state()
    print(f"\n토큰 재확인: token_obtained_at = {obtained_after}")
    unchanged = obtained_after == obtained_before
    check(unchanged, "프로덕션이 캐시된 access_token을 사용 (갱신 시도 없음)",
          "토큰이 갱신됐습니다 — 프로덕션의 카카오 자격증명이 정상일 수 있습니다."
          if not unchanged else "")

    print("\n" + "─" * 62)
    if unchanged and sent_ok:
        print("판정: 발송 단계는 정상. 앞서의 실패는 '토큰 갱신' 단계였다.")
        print("      → Render에 KAKAO_REST_API_KEY / KAKAO_CLIENT_SECRET이")
        print("        없거나 틀렸다는 진단이 확정됩니다.")
        print("      → 지금은 제가 갱신해 둔 access_token으로 버티는 중이며,")
        print(f"        약 {(expires_in - age) / 3600:.1f}시간 뒤 만료되면 다시 알림이 끊깁니다.")
    elif sent_ok:
        print("판정: 발송 성공. 다만 프로덕션이 토큰을 갱신했으므로 자격증명은 정상.")
        print("      앞서의 실패는 다른 원인 — 로그를 더 봐야 합니다.")
    else:
        print("판정: access_token이 유효한데도 발송이 실패했다.")
        print("      갱신이 아닌 발송 단계(동의항목 철회, 앱 상태 등) 문제입니다.")
    print("─" * 62)

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
    for label in failed:
        print(f"  · 실패: {label}")


if __name__ == "__main__":
    asyncio.run(main())
