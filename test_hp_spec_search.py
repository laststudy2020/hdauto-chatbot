"""마력·전압 기반 사양 역검색 검증.

고친 것: (1) 마력(HP) 파싱 (2) 트리거 어휘 확장 (3) 전압 등급 매칭
(4) 시리즈 한정. 회귀로 "가로채면 안 되는 문장"도 같이 본다.

  python test_hp_spec_search.py
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.core import clova as clova_mod
from app.core.intent import Intent, _hp_to_kw, classify_intent
from app.db.database import async_session

_real_chat = clova_mod.clova_client.chat_completion


async def _stub_chat(system_prompt: str, user_message: str, **kwargs) -> str:
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


# ─────────────────── [1] 마력 환산 ───────────────────
def test_hp_table() -> None:
    section("[1] 마력 → kW (매뉴얼 '적용 모터 중부하' 기준)")
    for hp, kw in ((0.5, 0.4), (1, 0.75), (2, 1.5), (3, 2.2), (5, 4.0),
                   (7.5, 5.5), (10, 7.5), (20, 15.0)):
        check(f"{hp}마력 → {kw}kW", _hp_to_kw(float(hp)) == kw,
              str(_hp_to_kw(float(hp))))
    # 표에 없는 값은 위 단계로 올린다(모터보다 작은 인버터를 권하면 안 됨).
    check("4마력 → 4.0kW(5마력 단계로 올림)", _hp_to_kw(4.0) == 4.0,
          str(_hp_to_kw(4.0)))
    check("200마력은 표 밖 → None", _hp_to_kw(200.0) is None, str(_hp_to_kw(200.0)))


# ─────────────────── [2] 의도 분류 ───────────────────
def test_intent() -> None:
    section("[2] 의도 분류 — 마력/어휘/시리즈")
    cases = [
        # (질문, 기대 intent, 기대 kW, 기대 시리즈)
        ("g100 3마력 380v 인버터 모델명 알려줘", Intent.SPEC_SEARCH, 2.2, "LSLV-G100"),
        ("380V 2.2kW 인버터 알려주세요", Intent.SPEC_SEARCH, 2.2, None),
        ("380V 2.2kW 인버터 추천해주세요", Intent.SPEC_SEARCH, 2.2, None),
        ("S100 220V 3마력 형명 알려줘", Intent.SPEC_SEARCH, 2.2, "LSLV-S100"),
        ("400V 10HP 인버터 뭐 쓰나요", Intent.SPEC_SEARCH, 7.5, None),
        # 가로채면 안 되는 것들 (회귀)
        ("LSLV0022S100-2 사이즈 알려주세요", Intent.SPECS, None, None),
        ("SV015iG5A-4 재고 있나요?", Intent.STOCK, None, None),
        ("SV015iG5A-4 OLt 떴습니다", Intent.ALARM, None, None),
        ("380V 2.2kW 인버터 재고 있나요", Intent.STOCK, None, None),
        ("매장 어디에 있나요?", Intent.LOCATION, None, None),
    ]
    for msg, want_intent, want_kw, want_series in cases:
        r = classify_intent(msg)
        check(f"{want_intent.value:12} ← {msg}", r.intent == want_intent,
              f"실제={r.intent.value}")
        if want_kw is not None:
            check(f"    kW={want_kw}", r.capacity_kw == want_kw, str(r.capacity_kw))
        if want_series is not None:
            check(f"    시리즈={want_series}", r.series_hint == want_series,
                  str(r.series_hint))


# ─────────────────── [3] 실제 응답 ───────────────────
QUERIES = [
    "g100 3마력 380v 인버터 모델명 알려줘",
    "380V 2.2kW 인버터 알려주세요",
    "S100 220V 3마력 형명 알려줘",
    "g100 380v 20마력 모델명 알려줘",
    "400V 10HP 인버터 뭐 쓰나요",
]


async def test_replies() -> None:
    section("[3] 실제 응답")
    from app.api.chatbot import _route

    async with async_session() as db:
        for q in QUERIES:
            r = classify_intent(q)
            reply, source = await _route(r, q, db)
            print(f"\n  Q: {q}")
            print(f"     intent={r.intent.value} source={source} "
                  f"kW={r.capacity_kw} HP={r.capacity_hp} series={r.series_hint}")
            for line in reply.splitlines():
                print(f"     | {line}")

    # 핵심 케이스는 정답을 못박는다.
    async with async_session() as db:
        q = "g100 3마력 380v 인버터 모델명 알려줘"
        r = classify_intent(q)
        reply, source = await _route(r, q, db)
        check("G100 3마력 380V → LSLV0022G100-4", "LSLV0022G100-4" in reply,
              reply[:120])
        check("환산 근거(중부하) 명시", "중부하" in reply, reply[:120])
        check("source=db_spec_search", source == "db_spec_search", source)


async def main() -> None:
    clova_mod.clova_client.chat_completion = _stub_chat
    test_hp_table()
    test_intent()
    await test_replies()
    clova_mod.clova_client.chat_completion = _real_chat
    print(f"\n{'=' * 74}\n결과: {passed} PASS / {failed} FAIL\n{'=' * 74}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
