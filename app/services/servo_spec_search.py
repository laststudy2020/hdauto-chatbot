"""서보드라이브 용량(W) 기반 추천 검색 + 모델별 상세조회 + 모터 역검색
(v8 — 드라이브 상세검색 시 단종/대체품/호환모터/타사비교 통합, 모터 검색시 호환드라이브 역검색 추가)"""
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Product, Specification, Replacement, ProductStatus
from app.services.replacement import find_replacement

_KNOWN_CAPACITIES_W = [50, 100, 200, 400, 500, 600, 750, 1000, 1500, 2000, 3000, 3500, 5000, 6000, 7000, 7500, 11000, 15000]


async def find_servo_by_capacity(capacity_w: float, db: AsyncSession) -> str:
    """용량(W)으로 서보앰프(제조사 무관) + 호환 서보모터 추천.
    2개 이상 매칭되면 속성별로 묶어서 비교하기 쉽게, 1개면 단일 블록으로 출력."""
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

    return _comparison_list(matches, capacity_w)


def _motor_text(s: Specification) -> str:
    """구체적 호환모터 리스트가 있으면 그대로, 없으면 안내문구로 대체"""
    motors = s.extra_specs.get("compatible_motors", [])
    if motors:
        return ", ".join(motors)
    note = s.extra_specs.get("motor_compat_note")
    return note if note else "-"


def _single_block(p: Product, s: Specification, capacity_w: float) -> str:
    weight = f", 무게 {s.weight_kg}kg" if s.weight_kg else ""
    interface = s.extra_specs.get("interface_note", "-")
    brake_note = s.extra_specs.get("brake_note", "")

    block = (
        f"**{p.manufacturer} {p.model_name}** ({capacity_w:g}W{weight})\n"
        f"인터페이스: {interface}\n"
        f"정격 출력전류: {s.extra_specs.get('rated_output_current_a', '-')}A\n"
        f"호환 서보모터: {_motor_text(s)}"
    )
    if brake_note:
        block += f"\n※ {brake_note}"
    return block


def _comparison_list(matches: list, capacity_w: float) -> str:
    """속성별로 묶어서 모델명: 값 형태의 리스트로 비교 출력 (마크다운 표 미지원 환경 대응)"""
    labels = [f"{p.manufacturer} {p.model_name}" for p, s in matches]

    def section(title, values):
        body = "\n".join(f"- {label}: {value}" for label, value in zip(labels, values))
        return f"**{title}**\n{body}"

    weight_vals = [f"{s.weight_kg}kg" if s.weight_kg else "-" for p, s in matches]
    current_vals = [
        f"{s.extra_specs.get('rated_output_current_a')}A"
        if s.extra_specs.get("rated_output_current_a") is not None else "-"
        for p, s in matches
    ]
    interface_vals = [s.extra_specs.get("interface_note", "-") for p, s in matches]
    motor_vals = [_motor_text(s) for p, s in matches]
    brake_vals = [s.extra_specs.get("brake_note", "-") for p, s in matches]

    parts = [
        f"🔧 **{capacity_w:g}W 서보드라이브 비교** ({len(matches)}개 모델)",
        section("무게", weight_vals),
        section("정격 출력전류", current_vals),
        section("인터페이스", interface_vals),
        section("호환 서보모터", motor_vals),
        section("브레이크 안내", brake_vals),
    ]
    return "\n\n".join(parts)


async def find_servo_drive_details(model_name: str, db: AsyncSession) -> str | None:
    """서보드라이브 모델명 검색 시 단종여부+대체품, 호환모터, 타사 동일용량 비교를 한 번에 안내.
    DB에 없거나 category가 'servo'가 아니면 None을 반환해 일반 SPECS 조회로 넘어가게 한다."""
    stmt = (
        select(Product)
        .options(selectinload(Product.specs))
        .where(
            or_(
                Product.model_name.ilike(f"%{model_name}%"),
                Product.series.ilike(f"%{model_name}%"),
            )
        )
    )
    result = await db.execute(stmt)
    product = result.scalars().first()

    if not product or product.category != "servo":
        return None

    sections = []

    # 1) 단종여부 + 대체품 + 타사 참고후보 (기존 find_replacement 재사용)
    replacement_info = await find_replacement(model_name, db)
    sections.append(replacement_info)

    # 2) 호환 서보모터
    if product.specs and product.specs.extra_specs:
        sections.append(f"🔩 **호환 서보모터**\n{_motor_text(product.specs)}")

    # 3) 타 제조사 동일 용량 비교
    capacity_w = (
        product.specs.extra_specs.get("capacity_w")
        if product.specs and product.specs.extra_specs else None
    )
    if capacity_w:
        comparison = await find_servo_by_capacity(capacity_w, db)
        sections.append(f"🏭 **{capacity_w:g}W 동일 용량 타사 비교**\n{comparison}")

    return "\n\n".join(sections)


async def find_drives_compatible_with_motor(motor_model: str, db: AsyncSession) -> str | None:
    """서보모터 모델명으로 호환되는 서보드라이브를 역으로 찾는다.
    매칭되는 드라이브가 없으면 None 반환 (모터 DB 자체가 없어 호출부에서 일반 조회로 넘어가게 함).
    모터 외형치수는 카탈로그 도면 미확보로 아직 제공 불가 — 안내 문구로 대체."""
    stmt = (
        select(Product, Specification)
        .join(Specification, Specification.product_id == Product.id)
        .where(Product.category == "servo")
    )
    result = await db.execute(stmt)
    rows = result.all()

    motor_key = motor_model.strip().lower()
    matched_drives = []
    for p, s in rows:
        if not s.extra_specs:
            continue
        motors = s.extra_specs.get("compatible_motors", [])
        if any(motor_key in m.lower() or m.lower() in motor_key for m in motors):
            matched_drives.append((p, s))

    if not matched_drives:
        return None

    drive_list = "\n".join(f"- {p.manufacturer} {p.model_name}" for p, s in matched_drives)

    return (
        f"**{motor_model}** 서보모터와 호환 가능한 서보드라이브:\n{drive_list}\n\n"
        f"⚠️ 모터 외형치수(가로/세로/높이)는 아직 카탈로그 도면을 확보하지 못해 안내드리기 어렵습니다. "
        f"치수가 필요하시면 현대자동화로 문의해 주세요."
    )
