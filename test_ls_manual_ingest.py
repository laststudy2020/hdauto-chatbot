"""LS 신형 인버터(S100/H100) 매뉴얼 적재 결과 자체 검증.

`ingest_ls_manual.py`로 넣은 알람코드/사양/치수가 실제로 조회되고, 새 코드가
의도 분류에서 알람으로 잡히는지 확인한다. CLOVA는 기본 스텁 — 검증 대상은
"DB에 맞는 행이 있고 프롬프트에 실려 가는가"이지 LLM 문장이 아니다.

  python test_ls_manual_ingest.py            # DB + 의도 분류 (빠름)
  python test_ls_manual_ingest.py --parse    # PDF 재파싱 회귀까지 (느림, 수십 초)
  python test_ls_manual_ingest.py --live     # 실제 CLOVA 호출 포함

DB는 .env(또는 환경변수) DATABASE_URL을 그대로 쓴다. 드라이런 DB로 돌리려면
  $env:DATABASE_URL = "sqlite+aiosqlite:///./dryrun_ls.db"
"""

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import func, select

from app.core import clova as clova_mod
from app.core.intent import Intent, classify_intent
from app.db.database import async_session
from app.db.models import AlarmCode, Product, Specification
from app.services.alarm import diagnose_alarm
from app.services.specs import lookup_specs

LIVE = "--live" in sys.argv
PARSE = "--parse" in sys.argv

_captured: list[str] = []
_real_chat = clova_mod.clova_client.chat_completion


async def _stub_chat(system_prompt: str, user_message: str, **kwargs) -> str:
    _captured.append(user_message)
    return "[STUB] LLM 응답"


passed = failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}   {detail}")


def section(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


# ─────────────────────────── [1] 파서 회귀 ───────────────────────────
# 좌표 정렬이 틀어지면 코드와 설명이 다른 항목끼리 묶인다. 조용히 틀리는 종류라
# 대표 코드 몇 개의 짝을 못박아 둔다.
S100_EXPECT = {
    "OLT": "Over Load",
    "OCT": "Over Current1",
    "OVT": "Over Voltage",
    "LVT": "Low Voltage",
    "GFT": "Ground Trip",
    "NMT": "No Motor Trip",
}


def test_parser() -> None:
    section("[1] 파서 회귀 (PDF 직접 재파싱)")
    from app.services.ls_manual_parser import parse_ls_manual

    s100 = parse_ls_manual(Path("manuals/LSLV-S100.pdf").read_bytes(), "S100")
    by_code = {a.code: a for a in s100.alarms}
    for code, name in S100_EXPECT.items():
        got = by_code.get(code)
        check(f"S100 {code} → {name}", got is not None and got.name == name,
              f"실제={got.name if got else '없음'}")
    check("S100 OCT 설명에 '과전류' 취지가 담김",
          "정격 전류의 200%" in (by_code.get("OCT").description if by_code.get("OCT") else ""),
          str(by_code.get("OCT")))

    models = {m.model_name: m for m in s100.models}
    m = models.get("LSLV0022S100-2")
    check("S100 LSLV0022S100-2 용량 2.2kW", m is not None and m.capacity_kw == 2.2,
          str(m))
    check("S100 LSLV0022S100-2 치수가 채워짐",
          m is not None and None not in (m.dimension_w, m.dimension_h, m.dimension_d),
          str(m))

    # G100은 PDF 텍스트 객체의 좌표가 실제 표 행과 어긋난다. 파서가 그걸
    # 감지해 스스로 버려야 한다 — 통과시키면 코드와 설명이 뒤섞여 들어간다.
    g100 = parse_ls_manual(Path("manuals/LSLV-G100.pdf").read_bytes(), "G100")
    check("G100은 정렬 불량으로 전량 폐기", len(g100.alarms) == 0,
          f"{len(g100.alarms)}건이 통과함")
    check("G100 폐기 사유가 note에 남음",
          any("어긋남" in n for n in g100.notes), str(g100.notes))


# ─────────────────────────── [2] DB 적재 ───────────────────────────
async def test_db(db) -> None:
    section("[2] DB 적재 상태")
    for series, min_alarms, min_models in (("LSLV-S100", 30, 25), ("LSLV-H100", 40, 30)):
        n_alarm = (await db.execute(
            select(func.count()).select_from(AlarmCode)
            .where(AlarmCode.product_series == series)
        )).scalar_one()
        n_model = (await db.execute(
            select(func.count()).select_from(Product).where(Product.series == series)
        )).scalar_one()
        check(f"{series} 알람 {n_alarm}건 (>= {min_alarms})", n_alarm >= min_alarms)
        check(f"{series} 제품 {n_model}건 (>= {min_models})", n_model >= min_models)

    n_spec = (await db.execute(
        select(func.count()).select_from(Specification)
        .join(Product, Product.id == Specification.product_id)
        .where(Product.series.in_(["LSLV-S100", "LSLV-H100"]))
    )).scalar_one()
    check(f"신형 사양 행 {n_spec}건", n_spec >= 55)

    row = (await db.execute(
        select(AlarmCode).where(AlarmCode.product_series == "LSLV-S100",
                                AlarmCode.alarm_code == "OLT")
    )).scalar_one_or_none()
    check("S100 OLT 행 존재", row is not None)
    if row:
        check("S100 OLT에 조치 사항이 채워짐", bool(row.solution and row.solution.strip()),
              repr(row.solution))
        check("S100 OLT 출처 페이지 기록", bool(row.manual_page), repr(row.manual_page))

    spec = (await db.execute(
        select(Specification).join(Product, Product.id == Specification.product_id)
        .where(Product.model_name == "LSLV0022S100-2")
    )).scalar_one_or_none()
    check("LSLV0022S100-2 사양 행 존재", spec is not None)
    if spec:
        check("치수 3면 모두 기록",
              None not in (spec.dimension_w, spec.dimension_h, spec.dimension_d),
              f"{spec.dimension_w}/{spec.dimension_h}/{spec.dimension_d}")
        check("중량 기록", spec.weight_kg is not None, str(spec.weight_kg))
        check("입력 전압 등급 기록", bool(spec.input_voltage), repr(spec.input_voltage))


# ─────────────────────────── [3] 의도 분류 ───────────────────────────
def test_intent() -> None:
    section("[3] 의도 분류 — 신형 코드 인식/오탐")
    cases = [
        ("S100 인버터에 OLT 떴어요", Intent.ALARM, True),
        ("LSLV0022S100-2 OVT 트립", Intent.ALARM, True),
        ("H100 NMT 뜨는데 어떻게 하나요", Intent.ALARM, True),
        ("S100 IOLW 경보가 계속 떠요", Intent.ALARM, True),
        # 아래 둘은 알람으로 잡히면 안 된다(일반 용어).
        ("PID 제어 설정은 어떻게 하나요", Intent.ALARM, False),
        ("IoT 연동 되나요?", Intent.ALARM, False),
        # 시리즈명이 같이 나오면 같은 단어라도 알람으로 본다.
        ("S100에서 PID 트립이 떴습니다", Intent.ALARM, True),
        # H100은 짧은 코드가 없고 LCD 명칭이 그대로 뜬다.
        ("H100에 Ground Trip 떠요", Intent.ALARM, True),
        ("H100 No Motor Trip 발생", Intent.ALARM, True),
    ]
    for msg, intent, should_match in cases:
        r = classify_intent(msg)
        ok = (r.intent == intent) == should_match
        check(f"{'알람' if should_match else '비알람'}: {msg}", ok,
              f"→ {r.intent} code={r.alarm_code}")

    # 형명이 안 잡히면 사양 조회가 "모델명을 입력해 주세요"로 끝난다.
    r = classify_intent("LSLV0022S100-2 외형 치수랑 중량 알려주세요")
    check("LSLV0022S100-2 형명 추출", r.model_name == "LSLV0022S100-2",
          f"→ {r.intent} model={r.model_name}")


# ─────────────────────────── [4] 알람 진단 ───────────────────────────
async def test_diagnose(db) -> None:
    section("[4] 알람 진단 — DB 매칭 및 컨텍스트")
    for code, expect_in_ctx in (("OLT", "Over Load"), ("NMT", "No Motor Trip")):
        _captured.clear()
        reply, matched = await diagnose_alarm(
            alarm_code=code, model_name=None,
            user_message=f"S100 인버터 {code} 뜹니다", db=db)
        check(f"{code} DB 매칭", matched, reply[:60])
        ctx = _captured[-1] if _captured else ""
        check(f"{code} 프롬프트에 '{expect_in_ctx}' 포함", expect_in_ctx in ctx,
              ctx[:120])

    # 시리즈 간 코드 표기가 겹친다(SV-iG5A 'OLt' vs LSLV-S100 'OLT').
    # 질문에 나온 시리즈가 앞으로 와야 엉뚱한 매뉴얼의 파라미터를 답하지 않는다.
    from app.services.alarm import _prefer_mentioned_series

    class _Row:
        def __init__(self, series):
            self.product_series = series

    rows = [_Row("SV-iG5A"), _Row("LSLV-S100"), _Row("LSLV-H100")]
    ordered = _prefer_mentioned_series(rows, "S100 인버터 OLT 떴어요")
    check("S100 언급 시 LSLV-S100 행이 최우선",
          ordered[0].product_series == "LSLV-S100",
          str([r.product_series for r in ordered]))
    ordered = _prefer_mentioned_series(rows, "SV015iG5A-4 OLt 떴어요")
    check("iG5A 언급 시 SV-iG5A 행이 최우선",
          ordered[0].product_series == "SV-iG5A",
          str([r.product_series for r in ordered]))

    # H100은 키패드에 명칭이 그대로 뜬다 — 명칭으로도 찾혀야 한다.
    _captured.clear()
    reply, matched = await diagnose_alarm(
        alarm_code="Ground Trip", model_name=None,
        user_message="H100 Ground Trip 떴어요", db=db)
    check("H100 명칭 조회(Ground Trip) 매칭", matched, reply[:60])


# ─────────────────────────── [5] 사양 조회 ───────────────────────────
async def test_specs(db) -> None:
    section("[5] 사양/치수 조회")
    _captured.clear()
    reply, found = await lookup_specs("LSLV0022S100-2", db)
    check("LSLV0022S100-2 사양 조회 성공", found, reply[:80])
    ctx = _captured[-1] if _captured else ""
    check("사양 컨텍스트에 치수가 실림",
          any(k in ctx for k in ("mm", "치수", "W", "100")), ctx[:150])


async def main() -> None:
    clova_mod.clova_client.chat_completion = _real_chat if LIVE else _stub_chat

    if PARSE:
        test_parser()
    else:
        print("\n(파서 회귀는 --parse 로 실행 — PDF 재파싱이라 수십 초 걸림)")

    test_intent()

    async with async_session() as db:
        await test_db(db)
        await test_diagnose(db)
        await test_specs(db)

    clova_mod.clova_client.chat_completion = _real_chat
    print(f"\n{'=' * 74}")
    print(f"결과: {passed} PASS / {failed} FAIL")
    print("=" * 74)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
