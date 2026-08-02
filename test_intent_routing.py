"""의도 분류(intent.classify_intent) 자체 검증 — 네트워크/DB 불필요.

CLAUDE.md에 문서화된 우선순위 체계가 실제로 그 순서대로 동작하는지 확인한다.
  iG5A 정확매칭 → 일반 알람 정규식 → 사양검색(전압+kW) → 서보용량(W)
  → 키워드 스코어링 → 모델명만 있을 때 폴백 → GENERAL

기대값은 "의도한 동작"으로 적는다. 실패는 스크립트 결함이 아니라 분류기의
실제 갭이므로, 실패 케이스는 하단에 갭으로 요약된다.

실행: python test_intent_routing.py
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.core.intent import Intent, classify_intent

SKIP = "__skip__"  # 해당 필드를 검사하지 않음

# (설명, 입력, 기대 intent, 기대 model_name, 기대 alarm_code)
CASES = [
    # ── 1) iG5A 전용 알람코드 정확매칭 (최우선) ──
    ("iG5A 코드 단독", "OCt 알람 원인 알려줘", Intent.ALARM, SKIP, "OCt"),
    ("iG5A 소문자 입력", "oc2 에러났어요", Intent.ALARM, SKIP, "OC2"),
    ("iG5A 한글 밀착", "OCt알람 원인", Intent.ALARM, SKIP, "OCt"),
    ("iG5A 저전압 코드", "Lut 뜨는데 어떻게 하나요", Intent.ALARM, SKIP, "Lut"),
    ("iG5A 4글자 코드", "FLtL 알람", Intent.ALARM, SKIP, "FLtL"),
    ("모델+iG5A코드", "SV015iG5A-4 OLt 에러", Intent.ALARM, "SV015iG5A-4", "OLt"),

    # ── 1-negative) 영문 단어 안의 부분일치는 알람이 아니어야 ──
    ("영단어 내부 COL 오탐 금지", "통신 PROTOCOL 규격 문의", Intent.SPECS, SKIP, None),
    ("영단어 내부 FAN 오탐 금지", "INFANTRY 라는 회사 아시나요", Intent.GENERAL, SKIP, None),

    ("영단어 내부 OU 오탐 금지", "TROUBLE 상황 문의드립니다", Intent.GENERAL, SKIP, None),

    # ── 2) 일반 알람 정규식 (미쓰비시 등) ──
    ("미쓰비시 AL.형식", "MR-J4-70A AL.E7 알람 원인", Intent.ALARM, "MR-J4-70A", "AL.E7"),
    ("미쓰비시 인버터 E.형식", "FR-E740-0.75K E.OC1 알람 해결법",
     Intent.ALARM, "FR-E740-0.75K", "E.OC1"),
    ("Err- 형식", "Err-04 에러 해결법", Intent.ALARM, SKIP, "ERR-04"),
    ("E숫자 형식", "E0001 알람 뜹니다", Intent.ALARM, SKIP, "E0001"),
    ("Fault 형식", "Fault 3 발생", Intent.ALARM, SKIP, "FAULT 3"),

    # ── 3) 사양 역검색 (전압 + kW + 추천 트리거) ──
    ("인버터 사양추천", "220V 2.2kW 인버터 추천해줘", Intent.SPEC_SEARCH, None, None),
    ("트리거 없으면 사양검색 아님", "220V 2.2kW 인버터 규격", Intent.SPECS, SKIP, None),

    # ── 4) 서보 용량(W) 추천 ──
    ("서보 W 추천", "400W 서보드라이브 추천해줘", Intent.SERVO_RECOMMEND, None, None),
    # 알려진 갭: WATT_PATTERN이 W만 보므로 kW로 물으면 SERVO_RECOMMEND에 못 간다.
    # 현재 동작(SPECS + 모델없음 → 되묻기)을 기대값으로 고정해 두고 별도 과제로 남긴다.
    ("[갭] kW 서보문의는 SPECS로 빠짐", "5kW 서보 추천해줘", Intent.SPECS, None, None),

    # ── 5) 키워드 스코어링 ──
    ("단종 대체품", "FX3U-32MT 단종되었나요 대체품 알려주세요",
     Intent.REPLACEMENT, "FX3U-32MT", None),
    ("재고 문의", "FX5U-32MT 재고 있나요?", Intent.STOCK, "FX5U-32MT", None),
    ("위치 문의", "현대자동화 위치 알려줘", Intent.LOCATION, SKIP, None),
    ("영업시간 문의", "영업시간이 어떻게 되나요", Intent.LOCATION, SKIP, None),
    ("규격 문의", "FX5U-32MT 외형 치수 알려주세요", Intent.SPECS, "FX5U-32MT", None),
    ("단가비교(관리자)", "경쟁사 최저가 확인해줘", Intent.PRICE_COMPARE, SKIP, None),

    # ── 6) 모델명만 있을 때 SPECS 폴백 ──
    ("미쓰비시 PLC 모델단독", "Q03UDVCPU", Intent.SPECS, "Q03UDVCPU", None),
    ("LS 서보 모델단독", "L7SA001A", Intent.SPECS, "L7SA001A", None),
    ("서보드라이브 모델단독", "MR-J4-40B", Intent.SPECS, "MR-J4-40B", None),

    # ── 7) 기본값 ──
    ("일반 인사", "안녕하세요", Intent.GENERAL, None, None),
]


def run() -> tuple[int, list[str]]:
    passed = 0
    failures = []

    print("=" * 78)
    print("의도 분류 검증 — classify_intent()")
    print("=" * 78)

    for desc, msg, exp_intent, exp_model, exp_alarm in CASES:
        r = classify_intent(msg)
        problems = []

        if r.intent != exp_intent:
            problems.append(f"intent {exp_intent.value} 기대 → {r.intent.value}")
        if exp_model is not SKIP and r.model_name != exp_model:
            problems.append(f"model {exp_model!r} 기대 → {r.model_name!r}")
        if exp_alarm is not SKIP and r.alarm_code != exp_alarm:
            problems.append(f"alarm {exp_alarm!r} 기대 → {r.alarm_code!r}")

        if problems:
            print(f"✘ {desc}")
            print(f"    입력: {msg}")
            for p in problems:
                print(f"    → {p}")
            failures.append(f"{desc} ({msg}): " + " / ".join(problems))
        else:
            print(f"✔ {desc:28} conf={r.confidence:.2f}")
            passed += 1

    return passed, failures


def main():
    passed, failures = run()
    total = len(CASES)

    print()
    print("=" * 78)
    print(f"결과: {passed}/{total} 통과 ({passed / total * 100:.0f}%)")
    if failures:
        print(f"\n확인된 갭 {len(failures)}건:")
        for f in failures:
            print(f"  · {f}")
    print("=" * 78)

    # 라우팅 커버리지 — 정의된 Intent 중 chatbot._route에 분기가 있는 것
    routed = {
        Intent.REPLACEMENT, Intent.SPECS, Intent.SPEC_SEARCH,
        Intent.SERVO_RECOMMEND, Intent.ALARM, Intent.LOCATION,
        Intent.STOCK, Intent.GENERAL,
    }
    unrouted = [i.value for i in Intent if i not in routed]
    print(f"\n라우팅 커버리지: {len(routed)}/{len(Intent)} "
          f"({len(routed) / len(Intent) * 100:.0f}%)")
    if unrouted:
        print(f"  전용 분기 없음(→ 일반 웹폴백으로 처리됨): {unrouted}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
