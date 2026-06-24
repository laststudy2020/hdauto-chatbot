import re
from dataclasses import dataclass
from enum import Enum


class Intent(str, Enum):
    REPLACEMENT = "replacement"      # 단종 대체품
    SPECS = "specs"                  # 규격/사이즈 (모델명 -> 사양)
    SPEC_SEARCH = "spec_search"      # 사양 -> 모델명 (역검색)
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
    capacity_kw: float | None = None  # 추출된 용량 kW (SPEC_SEARCH용)
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
_IG5A_ALT = "|".join(
    sorted((re.escape(c.upper()) for c in IG5A_ALARM_CODES), key=len, reverse=True)
)
IG5A_ALARM_PATTERN = rf"(?<![A-Za-z])(?:{_IG5A_ALT})(?![A-Za-z])"

# ─── 알람코드 패턴 (정규식, 미쓰비시 등 기타 제품군) ───
ALARM_PATTERNS = [
    r"AL[\.\-]?\s*[A-Z]?\d+",         # AL.E7, AL-17, AL.32
    r"[Ee]rr[\.\-]?\s*\d+",           # Err-04, Err.12
    r"E\d{2,4}",                       # E0001, E07
    r"[Ff]ault\s*\d+",                # Fault 3
    r"OL[12]?",                        # OL, OL1, OL2 (과부하)
    r"OC[123]?",                       # OC, OC1 (과전류)
    r"OU[123]?",                       # OU (과전압)
    r"LU",                             # LU (저전압)
    r"OH[123]?",                       # OH (과열)
]

# ─── 모델명 패턴 (FA 부품) ───
MODEL_PATTERNS = [
    r"[A-Z]{2,5}[\-]?\d{1,2}[A-Z][\-\d A-Z/]+",   # FX5U-32MT/ES, XBM-DR16S
    r"SV[\-]?\d{3}[a-zA-Z]+[\-\d]+",               # SV015iG5A-4
    r"MR[\-]?[A-Z]+\d+[A-Z]*[\-]?\d*[A-Z]*",       # MR-J4-70A
    r"[A-Z]\d{2}[A-Z]\d[\-\d\w]+",                  # E40H8-1024-3-T-24
    r"[A-Z]{2,6}[\-]\w{2,20}",                      # FR-E740-0.75K
]

# ─── 사양 기반 추천(SPEC_SEARCH) 패턴 ───
VOLTAGE_PATTERN = r"(\d{2,3})\s*[Vv]"
CAPACITY_PATTERN = r"(\d+(?:\.\d+)?)\s*[kK][wW]"
SPEC_SEARCH_TRIGGERS = [
    "추천", "추천해", "추천해줘", "어떤 모델", "어떤 제품",
    "맞는 모델", "맞는 제품", "맞는 인버터", "골라", "찾아줘",
]


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

    # 1.5) 사양 기반 추천 — 전압 + 용량 + 추천 트리거가 같이 있으면
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
    """텍스트에서 FA 부품 모델명 추출"""
    for pattern in MODEL_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group().strip()
    return None