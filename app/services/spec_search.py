import re

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Product, Specification, ProductStatus

# category 표기가 시리즈마다 다르다("inverter" / "인버터"). 영문만 걸러내던 탓에
# FR-E740·SV-iS7 등 한글 표기 제품이 사양 역검색에서 통째로 빠져 있었다.
INVERTER_CATEGORIES = ("inverter", "인버터")

# ─── 전압 등급 ───
# DB의 input_voltage 표기가 제각각이다("3상 400V", "삼상 380V", "AC380-480V").
# 고객이 380V라고 해도 카탈로그가 400V급으로 적어 두면 문자열 매칭은 실패한다.
# 실제로 G100/S100은 "3상 400V"라서 "380V" 질문에 한 건도 안 걸렸다.
_VOLTAGE_CLASSES = [(90, 130, "100V급"), (180, 260, "200V급"), (330, 500, "400V급")]


def _voltage_class(v: float) -> str | None:
    for low, high, label in _VOLTAGE_CLASSES:
        if low <= v <= high:
            return label
    return None


def _spec_voltage_classes(text: str | None) -> set[str]:
    """사양 문자열에 담긴 전압 등급들. '3상'의 3 같은 상수(相數)는 버린다."""
    if not text:
        return set()
    found = set()
    for num in re.findall(r"\d+(?:\.\d+)?", text):
        cls = _voltage_class(float(num))
        if cls:
            found.add(cls)
    return found


# LSLV 형명의 끝자리가 전압 등급을 확정한다(-1 단상200V, -2 3상200V, -4 3상400V).
# H100은 input_voltage가 대부분 NULL로 적재돼 전압 검색에 한 건도 안 걸렸다.
# 형명은 DB에 온전하므로 사양이 비었을 때 형명에서 등급을 복원한다.
_LSLV_SUFFIX_CLASS = {"1": "200V급", "2": "200V급", "4": "400V급"}
_LSLV_SUFFIX_PATTERN = re.compile(r"^LSLV\d{4}[A-Z]\d{3}-(\d)", re.IGNORECASE)


def _model_voltage_classes(model_name: str | None) -> set[str]:
    if not model_name:
        return set()
    m = _LSLV_SUFFIX_PATTERN.match(model_name)
    if not m:
        return set()
    cls = _LSLV_SUFFIX_CLASS.get(m.group(1))
    return {cls} if cls else set()


def _product_voltage_classes(p: Product) -> set[str]:
    spec = p.specs
    found = _spec_voltage_classes(spec.input_voltage if spec else None)
    return found or _model_voltage_classes(p.model_name)


def _rated_kw(text: str | None) -> float | None:
    """'2.2kW' → 2.2. 표기 흔들림(2.2kW/2.2 kW/2.20kW)을 문자열 비교로 맞추면
    조용히 어긋나므로 숫자로 비교한다."""
    if not text:
        return None
    m = re.match(r"\s*(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


def _describe(p: Product) -> str:
    s = p.specs
    parts = [f"'{p.model_name}'"]
    if s:
        detail = []
        if s.input_voltage:
            detail.append(f"전원 {s.input_voltage}")
        if s.rated_power:
            detail.append(f"정격출력 {s.rated_power}")
        if detail:
            parts.append("(" + ", ".join(detail) + ")")
    return " ".join(parts)


async def find_by_spec(
    voltage_v: int | None,
    capacity_kw: float | None,
    db: AsyncSession,
    series_hint: str | None = None,
    capacity_hp: float | None = None,
) -> str:
    """전압(V)과 용량(kW)으로 일치하는 인버터 모델을 찾는다.

    series_hint가 있으면 그 시리즈 안에서 먼저 찾고, 없을 때만 전체로 넓힌다.
    capacity_hp는 고객이 마력으로 물어봤을 때의 원래 값으로, 답변에 환산 근거를
    밝히는 데만 쓴다(어느 부하 기준인지 안 밝히면 한 단계 차이가 조용히 생긴다).
    """
    if voltage_v is None or capacity_kw is None:
        return (
            "전압과 용량을 같이 알려주시면 정확한 모델을 찾아드릴게요.\n"
            "예) 220V 2.2kW 인버터 추천해줘 / 380V 3마력 인버터 알려줘"
        )

    want_class = _voltage_class(voltage_v)
    if want_class is None:
        return (
            f"{voltage_v}V는 취급 인버터의 전압 등급(100V/200V/400V급)에 없습니다.\n"
            f"전압을 다시 확인해 주시거나 현대자동화로 문의해 주세요."
        )

    stmt = (
        select(Product)
        .join(Specification, Specification.product_id == Product.id)
        .options(selectinload(Product.specs))
        .where(
            or_(*[Product.category == c for c in INVERTER_CATEGORIES]),
            Product.status == ProductStatus.ACTIVE,
        )
    )
    candidates = (await db.execute(stmt)).scalars().all()

    matched = [
        p for p in candidates
        if want_class in _product_voltage_classes(p)
        and _rated_kw(p.specs.rated_power if p.specs else None) == capacity_kw
    ]

    # 마력으로 물어본 경우, 어느 부하 기준으로 환산했는지 먼저 밝힌다.
    head = ""
    if capacity_hp is not None:
        head = (
            f"{capacity_hp:g}마력은 중부하(정토크) 기준 {capacity_kw:g}kW입니다.\n"
            f"팬·펌프처럼 경부하로 쓰시면 한 단계 아래 용량도 적용됩니다.\n\n"
        )

    spec_label = f"{voltage_v}V({want_class}) {capacity_kw:g}kW"

    if series_hint:
        in_series = [p for p in matched if p.series == series_hint]
        if in_series:
            matched = in_series
        elif matched:
            others = ", ".join(_describe(p) for p in matched)
            return (
                f"{head}{series_hint}에는 {spec_label} 사양이 등록돼 있지 않습니다.\n"
                f"같은 사양의 다른 시리즈: {others}"
            )

    if not matched:
        scope = f"{series_hint} " if series_hint else ""
        return (
            f"{head}{scope}{spec_label} 사양에 맞는 인버터를 찾지 못했습니다.\n"
            f"정확한 사양을 다시 확인해 주시거나 현대자동화로 문의해 주세요."
        )

    if len(matched) == 1:
        p = matched[0]
        s = p.specs
        lines = [f"{head}{spec_label} 사양에 맞는 모델은 '{p.model_name}'입니다."]
        if s:
            lines.append(f"전원: {s.input_voltage} | 정격출력: {s.rated_power}")
            if s.dimension_w:
                lines.append(
                    f"외형: {s.dimension_w}x{s.dimension_h}x{s.dimension_d}mm"
                    + (f" | 무게: {s.weight_kg}kg" if s.weight_kg else "")
                )
        return "\n".join(lines)

    # 여러 시리즈가 같은 사양을 가진 경우 — 시리즈별로 묶어서 보여준다.
    by_series: dict[str, list[Product]] = {}
    for p in matched:
        by_series.setdefault(p.series or "기타", []).append(p)
    lines = [f"{head}{spec_label} 사양에 맞는 모델이 여러 개 있습니다."]
    for series, items in sorted(by_series.items()):
        names = ", ".join(_describe(p) for p in items)
        lines.append(f"- {series}: {names}")
    # 시리즈가 이미 하나로 좁혀졌는데 "시리즈를 알려달라"고 되물으면 대화가 막힌다.
    lines.append(
        "단상/삼상 중 어느 쪽인지 알려주시면 정확히 안내드릴게요."
        if len(by_series) == 1
        else "원하시는 시리즈를 알려주시면 정확히 안내드릴게요."
    )
    return "\n".join(lines)
