"""챗봇 전체 동작 스모크 테스트 — 9개 의도 전 경로.

chatbot._route를 그대로 태워 "질문이 의도한 서비스로 가서 의도한 source로
답이 나오는가"를 본다. 기존 스크립트들이 제품군별(LS 신형, 서보, 재고)로
쪼개져 있어 전 경로를 한 번에 보는 것이 없었다.

  python test_smoke_all.py           # CLOVA 스텁 (무료)
  python test_smoke_all.py --live    # 실제 CLOVA 호출

재고 알림(카카오)은 스텁으로 막는다. 실제 발송 점검은 별도 스크립트
(test_prod_notify_diagnosis.py)의 몫이다.
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.core import clova as clova_mod
from app.core.intent import Intent, classify_intent
from app.db.database import async_session

LIVE = "--live" in sys.argv

_real_chat = clova_mod.clova_client.chat_completion
_captured: list[str] = []
_notified: list[tuple] = []


async def _stub_chat(system_prompt: str, user_message: str, **kwargs) -> str:
    _captured.append(user_message)
    return "[STUB] LLM 응답"


async def _live_chat(system_prompt: str, user_message: str, **kwargs) -> str:
    _captured.append(user_message)
    return await _real_chat(system_prompt=system_prompt, user_message=user_message,
                            **kwargs)


async def _stub_notify(db, product, name, qty, state):
    _notified.append((name, qty, state))


async def _stub_slack(product_name):
    _notified.append(("slack", product_name))


passed = failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}   {detail}")


# (질문, 기대 intent, 기대 source 후보군)
# source가 db*로 나와야 하는 건은 DB 근거가 있다는 뜻이고, web_*는 폴백이다.
CASES = [
    # ── 단종 대체품 ──
    ("SV015iG5A-4 단종됐나요? 대체품 알려주세요", Intent.REPLACEMENT, None),
    ("대체품 문의합니다", Intent.REPLACEMENT, {"guide"}),
    # ── 규격/스펙 ──
    ("LSLV0022S100-2 사이즈 알려주세요", Intent.SPECS, {"db"}),
    ("MR-J4-40A 규격 알려주세요", Intent.SPECS, None),
    ("HG-KR43 사양 문의", Intent.SPECS, None),
    ("규격 문의드립니다", Intent.SPECS, {"guide"}),
    # ── 사양 역검색 ──
    ("380V 2.2kW 인버터 추천해주세요", Intent.SPEC_SEARCH, {"db_spec_search"}),
    ("400W 서보드라이브 뭐 쓰면 되나요", Intent.SERVO_RECOMMEND, {"db_servo_spec"}),
    # ── 알람 ──
    ("SV015iG5A-4 OLt 떴습니다", Intent.ALARM, {"db_alarm"}),
    ("S100 인버터에 OLT 트립", Intent.ALARM, {"db_alarm"}),
    ("H100에 Ground Trip 떠요", Intent.ALARM, {"db_alarm"}),
    ("G100 Over Current1 뜹니다", Intent.ALARM, {"db_alarm"}),
    ("FR-E740 E.OC1 알람 발생", Intent.ALARM, None),
    # ── 위치 ──
    ("매장 어디에 있나요?", Intent.LOCATION, {"fixed"}),
    # ── 재고 ──
    ("SV015iG5A-4 재고 있나요?", Intent.STOCK, {"db"}),
    ("재고 확인 부탁드려요", Intent.STOCK, {"guide"}),
    # ── 일반 ──
    ("배송 얼마나 걸리나요?", Intent.GENERAL, None),
]


async def main() -> None:
    clova_mod.clova_client.chat_completion = _live_chat if LIVE else _stub_chat

    from app.api import chatbot as chatbot_mod
    from app.services import inventory as inv_mod
    chatbot_mod.get_inventory_status = inv_mod.get_inventory_status
    inv_mod.notify_admins = _stub_notify
    inv_mod._notify_admin = _stub_slack

    from app.api.chatbot import _route

    async with async_session() as db:
        for message, want_intent, want_sources in CASES:
            _captured.clear()
            print(f"\n{'-' * 74}\nQ: {message}")
            r = classify_intent(message)
            try:
                reply, source = await _route(r, message, db)
            except Exception as e:
                check("응답 생성", False, f"{type(e).__name__}: {e}")
                continue

            print(f"  intent={r.intent.value} model={r.model_name} "
                  f"alarm={r.alarm_code!r} source={source}")
            print(f"  {reply[:220].replace(chr(10), ' | ')}")

            check(f"intent={want_intent.value}", r.intent == want_intent,
                  f"실제={r.intent.value}")
            if want_sources:
                check(f"source∈{sorted(want_sources)}", source in want_sources,
                      f"실제={source}")

    if _notified:
        print(f"\n(차단된 관리자 알림 {len(_notified)}건: {_notified})")

    clova_mod.clova_client.chat_completion = _real_chat
    print(f"\n{'=' * 74}\n결과: {passed} PASS / {failed} FAIL\n{'=' * 74}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
