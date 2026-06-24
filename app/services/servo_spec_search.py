"""서보드라이브 용량(W) 기반 추천 검색 (v4 — 여러 제조사 비교시 표로 출력)"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Product, Specification

_KNOWN_CAPACITIES_W = [50, 100, 200, 400, 500, 600, 750, 1000, 1500, 2000, 3000, 3500, 5000, 6000, 7000, 7500, 11000, 15000]


async def find_servo_by_capacity(capacity_w: float, db: AsyncSession) -> str:
    """용량(W)으로 서보앰프(제조사 무관) + 호환 서보모터 추천.
    2개 이상 매칭되면 비교하기 쉬운 표(markdown table)로, 1개면 단일 블록으로 출력."""
    stmt = (
        select(Product, Specification)
        .join(Specification, Specification.product_id == Product.id)
        .where(Product.category == "servo")
    )
    result = await db.execute(stmt)
    rows = result.all()

    matches = [
        (p, s) for p, s in rows
        if s.extra_specs and s.extra_specs.get("capacity_w") == capacity_w
    ]

    if not matches:
        known = ", ".join(f"{w}W" for w in sorted(set(_KNOWN_CAPACITIES_W)))
        return (
            f"{capacity_w:g}W 용량의 서보드라이브를 찾지 못했습니다.\n"
            f"등록된 용량: {known}"
        )

    matches.sort(key=lambda ps: (ps[0].manufacturer, ps[0].model_name))

    if len(matches) == 1:
        return _single_block(matches[0][0], matches[0][1], capacity_w)

    return _comparison_table(matches, capacity_w)


def _single_block(p: Product, s: Specification, capacity_w: float) -> str:
    motors = ", ".join(s.extra_specs.get("compatible_motors", [])) or "-"
    weight = f", 무게 {s.weight_kg}kg" if s.weight_kg else ""
    interface = s.extra_specs.get("interface_note", "-")
    brake_note = s.extra_specs.get("brake_note", "")

    block = (
        f"**{p.manufacturer} {p.model_name}** ({capacity_w:g}W{weight})\n"
        f"인터페이스: {interface}\n"
        f"정격 출력전류: {s.extra_specs.get('rated_output_current_a', '-')}A\n"
        f"호환 서보모터: {motors}"
    )
    if brake_note:
        block += f"\n※ {brake_note}"
    return block


def _comparison_table(matches: list, capacity_w: float) -> str:
    headers = [f"{p.manufacturer} {p.model_name}" for p, s in matches]

    def row(label, values):
        return f"| {label} | " + " | ".join(values) + " |"

    weight_vals = [f"{s.weight_kg}kg" if s.weight_kg else "-" for p, s in matches]
    current_vals = [f"{s.extra_specs.get('rated_output_current_a', '-')}A" for p, s in matches]
    interface_vals = [s.extra_specs.get("interface_note", "-") for p, s in matches]
    motor_vals = [", ".join(s.extra_specs.get("compatible_motors", [])) or "-" for p, s in matches]
    brake_vals = [s.extra_specs.get("brake_note", "-") for p, s in matches]

    lines = [
        row("항목", headers),
        "|---|" + "|".join(["---"] * len(headers)) + "|",
        row("용량", [f"{capacity_w:g}W"] * len(matches)),
        row("무게", weight_vals),
        row("정격 출력전류", current_vals),
        row("인터페이스", interface_vals),
        row("호환 서보모터", motor_vals),
        row("브레이크 안내", brake_vals),
    ]
    return "\n".join(lines)