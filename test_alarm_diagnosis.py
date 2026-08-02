"""알람코드 진단(alarm.diagnose_alarm + chatbot._route) 자체 검증.

CLOVA 호출은 기본적으로 스텁으로 대체한다 — 검증 대상은 "DB 매칭이 되는가,
매뉴얼 내용이 프롬프트에 실려 가는가, 폴백 분기가 맞는가"이지 LLM 문장이 아니다.
  python test_alarm_diagnosis.py         # 스텁 (DB만 사용, 무료/결정적)
  python test_alarm_diagnosis.py --live  # 실제 CLOVA 호출 포함
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from app.core import clova as clova_mod
from app.core.intent import classify_intent
from app.db.database import async_session
from app.db.models import AlarmCode
from app.core.intent import IG5A_ALARM_CODES
from app.services.alarm import diagnose_alarm

LIVE = "--live" in sys.argv

_captured: list[str] = []
_real_chat = clova_mod.clova_client.chat_completion


async def _stub_chat(system_prompt: str, user_message: str, **kwargs) -> str:
    _captured.append(user_message)
    return "[STUB] LLM 응답"


def use_stub():
    clova_mod.clova_client.chat_completion = _stub_chat


def use_real():
    clova_mod.clova_client.chat_completion = _real_chat


async def registered_codes(db) -> dict[str, list[str]]:
    """DB에 실제 적재된 알람코드 (제조사/시리즈별)."""
    rows = (await db.execute(select(AlarmCode.product_series, AlarmCode.alarm_code))).all()
    out: dict[str, list[str]] = {}
    for series, code in rows:
        out.setdefault(series, []).append(code)
    return out


async def test_ig5a_coverage(db) -> tuple[int, int, list[str]]:
    """intent.py의 iG5A 코드 23개가 전부 DB에서 매칭되는가."""
    print("\n" + "=" * 78)
    print("[1] iG5A 알람코드 전수 — 코드표(intent.py) vs DB 매칭")
    print("=" * 78)

    ok, miss = 0, []
    for code in IG5A_ALARM_CODES:
        _captured.clear()
        reply, matched = await diagnose_alarm(
            alarm_code=code, model_name=None, user_message=f"{code} 알람", db=db
        )
        if matched:
            ok += 1
        else:
            miss.append(code)
        print(f"  {'✔' if matched else '✘'} {code:6} matched={matched}")

    print(f"\n  DB 매칭 {ok}/{len(IG5A_ALARM_CODES)}건")
    if miss:
        print(f"  미등록 코드: {miss}")
    return ok, len(IG5A_ALARM_CODES), miss


async def test_manual_context(db) -> bool:
    """매칭 시 매뉴얼의 원인/해결/출처가 실제로 프롬프트에 실리는가."""
    print("\n" + "=" * 78)
    print("[2] 매칭 시 매뉴얼 컨텍스트 주입 확인")
    print("=" * 78)

    _captured.clear()
    reply, matched = await diagnose_alarm(
        alarm_code="OCt", model_name=None, user_message="OCt 알람 원인", db=db
    )
    if not _captured:
        print("  ✘ CLOVA 호출 자체가 없음")
        return False

    prompt = _captured[0]
    checks = {
        "공식 매뉴얼 검색 결과 헤더": "[공식 매뉴얼 검색 결과]" in prompt,
        "원인 필드": "원인:" in prompt,
        "해결 필드": "해결:" in prompt,
        "출처(매뉴얼/페이지)": "출처:" in prompt,
    }
    for label, passed in checks.items():
        print(f"  {'✔' if passed else '✘'} {label}")
    print(f"\n  주입된 프롬프트 앞부분:\n    {prompt[:160].replace(chr(10), ' / ')}")
    return matched and all(checks.values())


async def test_unmatched_fallback(db) -> bool:
    """DB에 없는 제조사 알람은 matched=False로 나와 웹/LLM 폴백으로 가는가."""
    print("\n" + "=" * 78)
    print("[3] 미등록 제조사 알람 → 폴백 분기 판정")
    print("=" * 78)

    cases = [
        ("미쓰비시 MR-J4", "AL.E7", "MR-J4-70A"),
        ("미쓰비시 FR-E700", "E.OC1", "FR-E740-0.75K"),
        ("존재하지 않는 코드", "ZZ99", None),
    ]
    all_ok = True
    for desc, code, model in cases:
        _captured.clear()
        reply, matched = await diagnose_alarm(
            alarm_code=code, model_name=model,
            user_message=f"{model or ''} {code} 알람", db=db,
        )
        # 미등록이면 matched=False여야 _route가 웹폴백으로 넘긴다
        expected_false = not matched
        print(f"  {'✔' if expected_false else '✘'} {desc:20} code={code:8} matched={matched}")
        if not expected_false:
            all_ok = False
            print(f"      → DB에 없는데 matched=True (오매칭)")
    return all_ok


async def test_or_condition_hazard(db) -> None:
    """alarm_code OR product_series 조건이 엉뚱한 행을 끌어오는지 확인."""
    print("\n" + "=" * 78)
    print("[4] OR 조건 부작용 — 모델명만으로도 매칭되는가")
    print("=" * 78)

    _captured.clear()
    reply, matched = await diagnose_alarm(
        alarm_code="ZZ99", model_name="SV-iG5A",
        user_message="SV-iG5A 알 수 없는 코드 ZZ99", db=db,
    )
    print(f"  코드는 미등록(ZZ99) + 시리즈는 등록(SV-iG5A) → matched={matched}")
    if matched:
        print("  ℹ️ OR 조건이라 코드가 틀려도 같은 시리즈 알람 5건이 컨텍스트로 실림")
        print(f"     (LLM이 무관한 알람 설명을 끌어다 쓸 여지 — 프롬프트 길이 {len(_captured[0])}자)")


async def test_route_integration(db) -> bool:
    """chatbot._route까지 통과했을 때 source 태그가 맞는가."""
    print("\n" + "=" * 78)
    print("[5] chatbot._route 통합 — 의도분류부터 응답까지")
    print("=" * 78)

    from app.api.chatbot import _route

    ok = True
    msg = "SV015iG5A-4 OLt 에러 원인이 뭔가요"
    ir = classify_intent(msg)
    reply, source = await _route(ir, msg, db)
    print(f"  입력: {msg}")
    print(f"  intent={ir.intent.value} model={ir.model_name!r} alarm={ir.alarm_code!r}")
    print(f"  {'✔' if source == 'db_alarm' else '✘'} source={source} (db_alarm 기대)")
    if source != "db_alarm":
        ok = False
    return ok


async def test_live_end_to_end(db) -> None:
    """실제 CLOVA 호출 — 답변이 매뉴얼 근거를 반영하는지 눈으로 확인."""
    print("\n" + "=" * 78)
    print("[6] 실제 CLOVA 호출 (--live)")
    print("=" * 78)

    use_real()
    try:
        reply, matched = await diagnose_alarm(
            alarm_code="OCt", model_name="SV-iG5A",
            user_message="SV-iG5A에서 OCt 알람이 뜹니다. 원인과 해결방법 알려주세요", db=db,
        )
        print(f"  matched={matched} / 응답 {len(reply)}자")
        print("  ─────────────────────────────────────────")
        for line in reply.splitlines()[:14]:
            print(f"  {line}")
    except Exception as e:
        print(f"  ✘ CLOVA 호출 실패: {type(e).__name__}: {e}")
    finally:
        use_stub()


async def main():
    use_stub()
    async with async_session() as db:
        print("=" * 78)
        print("알람코드 진단 검증" + ("  [LIVE]" if LIVE else "  [CLOVA 스텁]"))
        print("=" * 78)

        loaded = await registered_codes(db)
        print("\nDB 적재 현황:")
        for series, codes in loaded.items():
            print(f"  {series}: {len(codes)}건")

        ok, total, miss = await test_ig5a_coverage(db)
        ctx_ok = await test_manual_context(db)
        fb_ok = await test_unmatched_fallback(db)
        await test_or_condition_hazard(db)
        route_ok = await test_route_integration(db)

        if LIVE:
            await test_live_end_to_end(db)

        print("\n" + "=" * 78)
        print("요약")
        print("=" * 78)
        print(f"  iG5A 코드 DB 매칭      : {ok}/{total} ({ok / total * 100:.0f}%)")
        print(f"  매뉴얼 컨텍스트 주입   : {'통과' if ctx_ok else '실패'}")
        print(f"  미등록 알람 폴백 판정  : {'통과' if fb_ok else '실패'}")
        print(f"  _route 통합            : {'통과' if route_ok else '실패'}")
        print(f"  DB 적재 시리즈         : {list(loaded)}")


if __name__ == "__main__":
    asyncio.run(main())
