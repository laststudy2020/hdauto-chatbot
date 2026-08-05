import re
from dataclasses import dataclass
from enum import Enum


class Intent(str, Enum):
    REPLACEMENT = "replacement"      # 단종 대체품
    SPECS = "specs"                  # 규격/사이즈 (모델명 -> 사양)
    SPEC_SEARCH = "spec_search"      # 사양 -> 모델명 (역검색, 인버터: 전압+kW)
    SERVO_RECOMMEND = "servo_recommend"  # 서보 용량(W) -> 모델 추천
    ALARM = "alarm"                  # 고장 알람
    LOCATION = "location"            # 위치 안내
    STOCK = "stock"                  # 재고 문의
    PRICE_COMPARE = "price_compare"  # 단가 비교 (관리자)
    GENERAL = "general"              # 일반 문의


@dataclass
class IntentResult:
    intent: Intent
    model_name: str | None = None    # 추출된 모델명
    alarm_code: str | None = None    # 추출된 알람코드
    voltage_v: int | None = None     # 추출된 전압 (SPEC_SEARCH용)
    capacity_kw: float | None = None  # 추출된 용량 kW (SPEC_SEARCH용, 인버터)
    capacity_w: float | None = None   # 추출된 용량 W (SERVO_RECOMMEND용, 서보)
    confidence: float = 0.0


# ─── 키워드 기반 의도 분류 (1차 필터) ───
INTENT_KEYWORDS = {
    Intent.REPLACEMENT: [
        "단종", "대체", "대체품", "후속", "호환", "교체", "대안",
        "바꿀", "대신", "후속모델", "EOL", "단종품", "유사",
        "비슷한", "대체사양", "대체 모델", "상위모델"
    ],
    Intent.SPECS: [
        "규격", "사이즈", "크기", "치수", "외형", "스펙",
        "전압", "전원", "입출력", "통신", "무게", "중량",
        "dimension", "spec", "도면", "배선", "핀배열",
        "정격", "출력", "용량", "kW", "마력"
    ],
    Intent.ALARM: [
        "알람", "에러", "고장", "오류", "경보", "이상",
        "alarm", "error", "err", "fault", "warning",
        "AL.", "E.", "진단", "원인", "해결", "트러블슈팅",
        "깜빡", "멈춤", "안됨", "안 됨", "작동 안"
    ],
    Intent.LOCATION: [
        "위치", "주소", "찾아가", "길안내", "네비", "지도",
        "어디", "오시는", "방문", "매장", "사무실",
        "전화번호", "연락처", "영업시간"
    ],
    Intent.STOCK: [
        "재고", "수량", "있나요", "있어요", "몇개", "몇 개",
        "구매 가능", "입고", "품절", "stock"
    ],
    Intent.PRICE_COMPARE: [
        "단가 비교", "가격 비교", "경쟁사", "최저가",
        "가격 조정", "단가 조정", "시세", "마진"
    ],
}

# ─── LS iG5A 전용 알람코드 (매뉴얼 검증완료, 정확매칭 — 최우선 체크) ───
# 추측성 정규식(ALARM_PATTERNS)보다 먼저 검사한다.
# 대소문자 무관, 영문자에 바로 인접하지 않을 때만 매칭(단어 경계 대신
# 라틴 알파벳 인접 여부로 판단 — 한글과는 공백 없이 붙어도 정상 인식되도록).
IG5A_ALARM_CODES = [
    "OCt", "OC2", "GFt", "IOL", "OLt", "OHt", "POt", "Out", "Lut",
    "EtH", "COL", "FLtL", "EEP", "Hvt", "Err", "rErr", "COm", "FAn",
    "ESt", "EtA", "Etb", "ntC", "nbr",
]
_IG5A_CODE_MAP = {c.upper(): c for c in IG5A_ALARM_CODES}  # 매칭 결과 -> 원표기 복원

# 타사(미쓰비시 등)는 Err-04처럼 코드에 숫자를 붙여 쓴다. 숫자가 붙어 있으면
# iG5A의 'Err'로 가로채지 않고 아래 일반 정규식(ERR[\.\-]?\s*\d+)이 코드 전체를
# 잡도록 양보한다 — 가로채면 "-04"가 유실돼 엉뚱한 코드로 조회된다.
_NUMBERED_BY_OTHERS = {"ERR", "RERR"}
_IG5A_ALT = "|".join(
    re.escape(code) + (r"(?![\.\-]?\s*\d)" if code in _NUMBERED_BY_OTHERS else "")
    for code in sorted((c.upper() for c in IG5A_ALARM_CODES), key=len, reverse=True)
)
IG5A_ALARM_PATTERN = rf"(?<![A-Za-z])(?:{_IG5A_ALT})(?![A-Za-z])"

# ─── LS 신형 인버터(S100/G100/H100) 키패드 표시 코드 ───
# LSLV-S100 매뉴얼 p.429~433 고장/경보표에서 좌표 기반으로 추출한 실제 표기다
# (ingest_ls_manual.py 참조). H100은 키패드에 명칭이 그대로 뜨므로 코드가 없다.
LS_NEW_ALARM_CODES = [
    "OLT", "ULT", "OCT", "OVT", "LVT", "LV2", "GFT", "ETH", "POT", "IPO",
    "IOL", "NMT", "OHT", "OC2", "HWT", "NTC", "XBR", "SFA", "SFB", "LOR",
    "ERRC", "OLW", "ULW", "IOLW", "LCW", "EFAN", "FANW", "DBW", "TRER",
]
# 아래는 일반 용어로도 흔히 쓰인다("PID 제어", "IoT 연동", "옵션(OPT)").
# 시리즈명이 함께 나올 때만 알람으로 본다 — 아니면 평범한 질문이 알람으로 샌다.
LS_AMBIGUOUS_ALARM_CODES = ["EXT", "BS", "PID", "OPT", "IOT", "HOLD", "PAR", "SLP"]
LS_SERIES_PATTERN = r"(?:LSLV|[SGH]100)"


def _code_alternation(codes: list[str]) -> str:
    return "|".join(sorted((c.upper() for c in codes), key=len, reverse=True))


LS_NEW_ALARM_PATTERN = (
    rf"(?<![A-Za-z0-9])(?:{_code_alternation(LS_NEW_ALARM_CODES)})(?![A-Za-z0-9])"
)
LS_AMBIGUOUS_ALARM_PATTERN = (
    rf"(?<![A-Za-z0-9])(?:{_code_alternation(LS_AMBIGUOUS_ALARM_CODES)})(?![A-Za-z0-9])"
)

# H100은 LCD 키패드라 짧은 코드가 없고 명칭이 그대로 표시된다. 고객은 화면에
# 보이는 대로 "Ground Trip 떠요"라고 쓰므로 명칭도 알람으로 잡아야 한다.
# (LSLV-H100 매뉴얼 p.489~503 고장표 기준)
LS_LCD_TRIP_NAMES = [
    "Over Load", "Under Load", "Over Current1", "Over Current2", "Over Voltage",
    "Low Voltage2", "Low Voltage", "Ground Trip", "E-Thermal", "Out Phase Open",
    "In Phase Open", "Inverter OLT", "No Motor Trip", "Over Heat", "External Trip",
    "H/W-Diag", "NTC Open", "In Fan Trip", "Fan Trip", "Thermal Trip",
    "Lost Key Pad", "Lost Keypad", "Fuse Open", "Damper Err", "MMC Interlock",
    "Clean RPT Err", "Pipe Broken", "Broken Belt", "Lost Command", "IO Board Trip",
    "TB Trip", "Para Write Trip", "Para Write Fail", "Option Trip", "INV Over Load",
    "Fan Warning", "In Fan Warning", "Fan Ex Change", "Low Battery",
    "Rs Tune Err", "Lsig Tune Err", "Safety A(B)Err", "Ext-Brake", "Pre-PID",
]
_LCD_ALT = "|".join(
    re.escape(n).replace(r"\ ", r"\s+")
    for n in sorted(LS_LCD_TRIP_NAMES, key=len, reverse=True)
)
LS_LCD_TRIP_PATTERN = rf"(?<![A-Za-z])(?:{_LCD_ALT})(?![A-Za-z])"

# 명칭은 평범한 영어 조합("over voltage")이기도 해서, 시리즈명이나 고장 문맥이
# 같이 있을 때만 알람으로 본다.
ALARM_CONTEXT_WORDS = ("트립", "고장", "알람", "에러", "경보",
                       "떴", "뜹", "떠요", "떠서", "발생", "trip", "Trip", "TRIP")

# ─── 알람코드 패턴 (정규식, 미쓰비시 등 기타 제품군) ───
# 경계 조건이 없으면 모델명·영단어 내부를 알람코드로 오인한다. 실제로
# "FR-E740-0.75K E.OC1"에서 모델명 조각인 E740이 코드로 잡혀 진짜 코드가
# 유실됐고, "통신 PROTOCOL"의 OL, "TROUBLE"의 OU도 알람으로 오분류됐다.
# 앞쪽에 하이픈까지 막는 이유: 모델명은 FR-E740처럼 하이픈으로 이어 붙는다.
_ALARM_LEAD = r"(?<![A-Za-z0-9\-])"
_ALARM_TAIL = r"(?![A-Za-z0-9])"

# 아래 본문은 classify_intent가 msg_upper(전부 대문자)에 매칭하므로 반드시
# 대문자로 적는다. 예전의 [Ee]rr / [Ff]ault는 소문자를 요구해 영원히 매칭되지
# 않는 죽은 패턴이었다.
_ALARM_BODIES = [
    r"AL[\.\-]?\s*[A-Z]?\d+",          # AL.E7, AL-17, AL.32
    r"E[\.\-][A-Z]{1,4}\d{0,2}",       # E.OC1, E.THT (미쓰비시 인버터)
    r"ERR[\.\-]?\s*\d+",               # Err-04, Err.12
    r"FAULT\s*\d+",                    # Fault 3
    r"E\d{2,4}",                       # E0001, E07
    r"OL[12]?",                        # OL, OL1, OL2 (과부하)
    r"OC[123]?",                       # OC, OC1 (과전류)
    r"OU[123]?",                       # OU (과전압)
    r"LU",                             # LU (저전압)
    r"OH[123]?",                       # OH (과열)
]
ALARM_PATTERNS = [_ALARM_LEAD + body + _ALARM_TAIL for body in _ALARM_BODIES]

# ─── 모델명 패턴 (FA 부품) ───
MODEL_PATTERNS = [
    # 공백을 문자군에서 뺐다. 포함돼 있으면 "SV015iG5A-4 OLt"처럼 모델명 뒤의
    # 공백과 알람코드까지 삼켜 DB 조회가 실패한다.
    r"[A-Z]{2,5}[\-]?\d{1,2}[A-Z][\-\dA-Z/]+",     # FX5U-32MT/ES, XBM-DR16S
    r"SV[\-]?\d{3}[A-Za-z0-9]*(?:[\-]\d+)?",       # SV015iG5A-4
    # LS 신형 인버터. 숫자가 알파벳 사이에 끼어 있어 위 패턴들에 걸리지 않는다
    # — 없으면 "LSLV0022S100-2 치수 알려주세요"가 모델명 없이 안내문만 돌려준다.
    r"LSLV\d{4}[SGHML]100(?:[\-]\d)?",             # LSLV0022S100-2
    r"MR[\-]?[A-Z]+\d+[A-Z]*[\-]?\d*[A-Z]*",       # MR-J4-70A
    r"[A-Z]\d{2}[A-Z]\d[\-\d\w]+",                  # E40H8-1024-3-T-24
    r"[A-Z]{2,6}[\-][\w\.\-/]{2,20}",               # FR-E740-0.75K
    r"L7S[A-Z]\d{3}[A-Z]?", 
    r"[A-Z]\d{2,3}[A-Z]{2,6}",                      # Q03UDVCPU, Q06UDVCPU, L02SCPU (미쓰비시 Q/L PLC)                        # L7SA001A (LS 서보드라이브, 대시 없음)
]

# ─── 사양 기반 추천(SPEC_SEARCH) 패턴 — 인버터: 전압(V) + 용량(kW) ───
VOLTAGE_PATTERN = r"(\d{2,3})\s*[Vv]"
CAPACITY_PATTERN = r"(\d+(?:\.\d+)?)\s*[kK][wW]"
SPEC_SEARCH_TRIGGERS = [
    "추천", "추천해", "추천해줘", "어떤 모델", "어떤 제품",
    "맞는 모델", "맞는 제품", "맞는 인버터", "골라", "찾아줘",
]

# ─── 서보 용량 추천(SERVO_RECOMMEND) 패턴 — 용량(W)만으로 검색 ───
# kW 표기와 겹치지 않도록 앞에 k/K가 오면 제외, 뒤에 추가 영문자가 오면 제외
WATT_PATTERN = r"(?<![kK])(\d+(?:\.\d+)?)\s*[wW](?![a-zA-Z])"


def classify_intent(message: str) -> IntentResult:
    """사용자 메시지에서 의도와 엔티티를 추출"""
    msg = message.strip()
    msg_upper = msg.upper()

    # 0) iG5A 전용 알람코드 정확매칭 (최우선 — 매뉴얼 검증된 코드)
    ig5a_match = re.search(IG5A_ALARM_PATTERN, msg_upper)
    if ig5a_match:
        alarm_code = _IG5A_CODE_MAP.get(ig5a_match.group(), ig5a_match.group())
        model = _extract_model(msg)
        return IntentResult(
            intent=Intent.ALARM,
            alarm_code=alarm_code,
            model_name=model,
            confidence=0.97,
        )

    # 0.5) LS 신형(S100/G100/H100) 키패드 코드 정확매칭 — 매뉴얼에서 추출한 목록
    ls_match = re.search(LS_NEW_ALARM_PATTERN, msg_upper)
    if not ls_match and re.search(LS_SERIES_PATTERN, msg_upper):
        ls_match = re.search(LS_AMBIGUOUS_ALARM_PATTERN, msg_upper)
    if not ls_match and (re.search(LS_SERIES_PATTERN, msg_upper)
                         or any(k in msg for k in ALARM_CONTEXT_WORDS)):
        ls_match = re.search(LS_LCD_TRIP_PATTERN, msg, re.IGNORECASE)
    if ls_match:
        return IntentResult(
            intent=Intent.ALARM,
            alarm_code=ls_match.group(),
            model_name=_extract_model(msg),
            confidence=0.97,
        )

    # 1) 알람코드 패턴 체크 (그 외 제품군 — 미쓰비시 등)
    for pattern in ALARM_PATTERNS:
        match = re.search(pattern, msg_upper)
        if match:
            alarm_code = match.group().strip()
            model = _extract_model(msg)
            return IntentResult(
                intent=Intent.ALARM,
                alarm_code=alarm_code,
                model_name=model,
                confidence=0.95,
            )

    # 1.5) 사양 기반 추천(인버터) — 전압 + 용량(kW) + 추천 트리거가 같이 있으면
    # ("kW", "전압" 등은 SPECS 키워드와도 겹치므로, 일반 키워드 매칭보다 먼저 체크해서 가로챈다)
    voltage_match = re.search(VOLTAGE_PATTERN, msg)
    capacity_match = re.search(CAPACITY_PATTERN, msg, re.IGNORECASE)
    has_trigger = any(t in msg for t in SPEC_SEARCH_TRIGGERS)
    if voltage_match and capacity_match and has_trigger:
        return IntentResult(
            intent=Intent.SPEC_SEARCH,
            voltage_v=int(voltage_match.group(1)),
            capacity_kw=float(capacity_match.group(1)),
            confidence=0.9,
        )

    # 1.6) 서보 용량(W) 기반 추천 — 전압 구분이 없는 서보 시리즈는 용량(W)만으로 검색
    watt_match = re.search(WATT_PATTERN, msg)
    if watt_match and has_trigger:
        return IntentResult(
            intent=Intent.SERVO_RECOMMEND,
            capacity_w=float(watt_match.group(1)),
            confidence=0.9,
        )

    # 2) 키워드 매칭으로 의도 분류
    scores: dict[Intent, int] = {}
    for intent, keywords in INTENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in msg.lower())
        if score > 0:
            scores[intent] = score

    # 3) 모델명 추출
    model = _extract_model(msg)

    # 4) 최고 스코어 의도 결정
    if scores:
        best_intent = max(scores, key=scores.get)
        confidence = min(scores[best_intent] / 3.0, 1.0)
        return IntentResult(
            intent=best_intent,
            model_name=model,
            confidence=confidence,
        )

    # 5) 모델명만 있고 의도 키워드 없으면 → 일반 제품 문의 (스펙 우선)
    if model:
        return IntentResult(
            intent=Intent.SPECS,
            model_name=model,
            confidence=0.5,
        )

    # 6) 기본값
    return IntentResult(intent=Intent.GENERAL, confidence=0.3)


def _extract_model(text: str) -> str | None:
    """텍스트에서 FA 부품 모델명 추출.

    패턴 목록 순서대로 "먼저 걸리는 것"을 쓰면, 뒤쪽 패턴이 더 정확해도
    앞 패턴이 문자열 뒷부분에서 잡은 조각에 밀린다. 실제로 "SV015iG5A-4"가
    범용 패턴에 'iG5A-4'로 잘려 나갔다. 후보를 모두 모아 가장 긴 것을 쓴다.
    """
    best: str | None = None
    for pattern in MODEL_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            candidate = match.group().strip().rstrip(".,")
            if best is None or len(candidate) > len(best):
                best = candidate
    return best
