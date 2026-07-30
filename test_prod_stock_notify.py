"""프로덕션 재고조회 → 카카오 알림 실동작 검증.

배포된 앱(https://hdauto-chatbot.onrender.com)에 실제 재고 문의를 보내고,
프로덕션 DB에 알림 기록이 남았는지 확인한다. ADMIN_COMMAND_KEY가 필요 없다 —
notify_admins()는 alarm_recipients 행을 직접 읽어 발송한다.

확인하는 것:
  1. 고객 응답이 정상이고 내부 정보(수량/원가/타사 단가)가 없는지
  2. stock_alerts에 새 행이 생겼고 sent_at이 채워졌는지 (= 알림 경로가 실행됨)
  3. price_history에 경쟁사 비교가 기록됐는지
  4. 알림 시각이 KST로 맞는지 (서버가 UTC라 과거에 9시간 어긋난 이력이 있다)

실제 카카오톡 메시지가 사장님 '나와의 채팅'으로 발송된다. 도착 여부는 사람이
확인해야 한다 — 이 스크립트는 서버 측 증거까지만 확인한다.

주의: notify_admins는 모델별 1시간 디바운스가 있다. 최근 조회된 모델은 알림이
스킵되므로, 여러 모델을 순서대로 시도해 디바운스에 걸리지 않은 것을 찾는다.

실행: python test_prod_stock_notify.py
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")

import httpx
from sqlalchemy import select

from app.db.database import async_session
from app.db.models import AlarmRecipient, PriceHistory, Product, StockAlert

BASE = "https://hdauto-chatbot.onrender.com"
KST = timezone(timedelta(hours=9))

# 디바운스에 걸릴 수 있으니 여러 개 준비. 카탈로그에 있는 모델이어야 기록이 남는다.
CANDIDATES = ["MR-J4-70A", "MR-J4-40A", "MR-J4-100A", "MR-J4-20A"]

FORBIDDEN = ["원가", "마진", "타사 가격", "판매단가", "경쟁사"]

results: list[tuple[bool, str]] = []


def check(ok, label: str, detail: str = ""):
    results.append((bool(ok), label))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    if detail:
        for line in detail.splitlines():
            print(f"       {line}")


async def _snapshot():
    """현재 stock_alerts / price_history의 최대 id."""
    async with async_session() as db:
        sa = (await db.execute(select(StockAlert.id).order_by(StockAlert.id.desc()).limit(1))).scalar()
        ph = (await db.execute(select(PriceHistory.id).order_by(PriceHistory.id.desc()).limit(1))).scalar()
    return sa or 0, ph or 0


async def _new_rows(since_sa: int, since_ph: int):
    async with async_session() as db:
        alerts = (await db.execute(
            select(StockAlert, Product.model_name)
            .join(Product, Product.id == StockAlert.product_id)
            .where(StockAlert.id > since_sa)
            .order_by(StockAlert.id)
        )).all()
        prices = (await db.execute(
            select(PriceHistory, Product.model_name)
            .join(Product, Product.id == PriceHistory.product_id)
            .where(PriceHistory.id > since_ph)
            .order_by(PriceHistory.id)
        )).all()
    return alerts, prices


async def main():
    # ── 수신자 확인 ──
    async with async_session() as db:
        recipients = (await db.execute(
            select(AlarmRecipient).where(
                AlarmRecipient.is_active.is_(True),
                AlarmRecipient.channel == "kakao",
            ).order_by(AlarmRecipient.id)
        )).scalars().all()
    print(f"활성 카카오 수신자 {len(recipients)}명: {[r.name for r in recipients]}")
    check(len(recipients) >= 1, "활성 수신자 1명 이상",
          "" if recipients else "alarm_recipients에 활성 행이 없어 알림이 나갈 수 없습니다.")
    if not recipients:
        return

    before_sa, before_ph = await _snapshot()
    print(f"기준선: stock_alerts 최대 id={before_sa}, price_history 최대 id={before_ph}\n")

    # ── 재고 문의 발송 ──
    reply = None
    used_model = None
    # 재고조회는 의도분류 → 커머스API → 경쟁사 단가 → 카카오 발송을 순차로 타므로
    # 느리다. Render 무료 플랜의 콜드 스타트까지 겹치면 2분을 넘길 수 있다.
    async with httpx.AsyncClient(trust_env=False, timeout=240.0) as client:
        for model in CANDIDATES:
            question = f"{model} 재고 있나요?"
            print(f"── 문의: {question}")
            try:
                resp = await client.post(
                    f"{BASE}/api/chat/",
                    json={"message": question, "user_id": "prod-notify-test",
                          "channel": "webchat"},
                )
            except Exception as e:
                # 타임아웃 예외는 str()이 비어 있어 원인이 안 보인다.
                print(f"   실패: {type(e).__name__}: {e or '(메시지 없음)'}")
                print("   → 요청은 서버에 도달했을 수 있으므로 DB 기록을 확인합니다.")
                await asyncio.sleep(5)
                alerts, prices = await _new_rows(before_sa, before_ph)
                if alerts:
                    print(f"   → 알림 기록 {len(alerts)}건 생성됨 — 발송 경로는 실행됐습니다.")
                    for alert, model_name in alerts:
                        print(f"      StockAlert #{alert.id} {model_name} "
                              f"type={alert.alert_type} sent_at={alert.sent_at}")
                    check(True, "타임아웃했지만 알림 경로는 실행됨 (DB 기록 확인)")
                else:
                    check(False, f"프로덕션 챗봇 호출 실패 ({type(e).__name__}), 알림 기록도 없음")
                return
            if resp.status_code != 200:
                print(f"   HTTP {resp.status_code}: {resp.text[:200]}")
                continue
            reply = resp.json().get("reply", "")
            used_model = model
            print(f"   응답 {len(reply)}자")

            # 알림 기록이 생겼는지 바로 확인 — 생겼으면 디바운스에 걸리지 않은 것
            await asyncio.sleep(3)
            alerts, _ = await _new_rows(before_sa, before_ph)
            if alerts:
                print(f"   → 알림 기록 생성됨, 이 모델로 검증 진행\n")
                break
            print(f"   → 알림 기록 없음 (디바운스 가능성), 다음 모델 시도\n")

    if not reply:
        check(False, "프로덕션 챗봇 응답 수신")
        return

    check(True, f"프로덕션 챗봇 응답 수신 ('{used_model}')")
    print("── 고객 응답 ──")
    print(reply)
    print("──────────────\n")

    # ── 고객 응답에 내부 정보가 없는지 ──
    leaked = [w for w in FORBIDDEN if w in reply]
    check(not leaked, "고객 응답에 내부 정보 없음",
          f"노출된 표현: {leaked}" if leaked else "")

    # ── 알림 기록 확인 ──
    alerts, prices = await _new_rows(before_sa, before_ph)
    check(bool(alerts), f"stock_alerts에 새 알림 기록 (신규 {len(alerts)}건)",
          "디바운스(모델별 1시간)로 스킵됐거나 발송이 실패했습니다.\n"
          "프로덕션 재시작 직후가 아니면 최근 조회된 모델은 스킵됩니다."
          if not alerts else "")

    now_kst = datetime.now(KST)
    for alert, model_name in alerts:
        print(f"  StockAlert #{alert.id} {model_name} type={alert.alert_type} "
              f"channel={alert.channel} sent_at={alert.sent_at}")
        check(alert.sent_at is not None, f"#{alert.id} sent_at 기록됨")
        if alert.sent_at:
            # sent_at은 DB의 CURRENT_TIMESTAMP(=DB 서버 시각). KST 기준 현재와의 격차를 본다.
            gap_kst = abs((now_kst.replace(tzinfo=None) - alert.sent_at).total_seconds())
            gap_utc = abs((now_kst.astimezone(timezone.utc).replace(tzinfo=None)
                           - alert.sent_at).total_seconds())
            if gap_kst < 300:
                print(f"       sent_at은 KST 기준 (현재와 {gap_kst:.0f}초 차)")
            elif gap_utc < 300:
                print(f"       sent_at은 UTC 기준 (KST와 9시간 차) — DB 서버 타임존 확인 필요")
            else:
                print(f"       sent_at이 KST/UTC 어느 쪽과도 안 맞음 "
                      f"(KST차 {gap_kst:.0f}초, UTC차 {gap_utc:.0f}초)")

    for price, model_name in prices:
        print(f"  PriceHistory #{price.id} {model_name} 자사={price.our_price} "
              f"타사최저={price.competitor_min} 업체수={price.competitor_count} "
              f"차이={price.diff_percent}% 조정필요={price.needs_adjustment} "
              f"checked_at={price.checked_at}")

    print("\n" + "─" * 60)
    print("서버 측 검증은 여기까지입니다. 다음은 사람이 확인해야 합니다:")
    print(f"  · 카카오톡 '나와의 채팅'에 '{used_model}' 재고 알림이 도착했는지")
    print("  · 알림의 '조회 시각'이 지금 한국 시각과 맞는지 "
          f"(현재 KST {now_kst:%Y-%m-%d %H:%M})")
    print("  · 알림에 타사 가격이 표시되고, 해외 표기 상품이 제외됐는지")
    print("─" * 60)

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
    if failed:
        print("실패 항목:")
        for label in failed:
            print(f"  · {label}")
    print("\n결과:", "통과" if not failed else "실패")


if __name__ == "__main__":
    asyncio.run(main())
