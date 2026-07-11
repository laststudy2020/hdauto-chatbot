"""서보드라이브 용량(W) 기반 추천 검색 + 모델별 상세조회 + 모터 역검색
(v9 — 드라이브 상세검색 시 단종/대체품/호환모터/타사비교 통합, 모터 검색시 호환드라이브 역검색 추가,
감속기 결합사양(motor_specs) 양방향 검색 추가)"""
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Product, Specification, Replacement, ProductStatus, Reducer

_KNOWN_CAPACITIES_W = [50, 100, 200, 400, 500, 600, 750, 1000, 1500, 2000, 3000, 3500, 5000, 6000, 7000, 7500, 11000, 15000]

# ─── 모터 물리치수/감속기 결합사양 안내 시 항상 첨부하는 면책 문구 (단일 관리 지점) ───
_DIMENSION_DISCLAIMER = (
    "\n\n⚠️ 위 치수 및 결합 사양은 참고용이며, 실제 장착 전 반드시 제조사 도면 또는 "
    "현대자동화로 직접 확인 부탁드립니다. 치수 오류로 인한 기계적 손상은 책임지지 않습니다."
)


def _with_dimension_disclaimer(text: str) -> str:
    return text + _DIMENSION_DISCLAIMER


# ─── 감속기 자동매칭 결과에 항상 첨부하는 어댑터 확인 문구 (치수 disclaimer와 별개) ───
_REDUCER_ADAPTER_DISCLAIMER = (
    "\n\n🔩 정확한 모터 장착 어댑터(C1~C10)는 APEX 측 확인이 필요합니다."
)


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


async def get_servo_companion_note(
    product: Product | None, model_name: str, db: AsyncSession
) -> str:
    """서보 계열 문의면 호환 가능한 짝(드라이브↔모터) 정보를 반환.

    - product가 서보드라이브(category=='servo')면 → 호환 서보모터 한 줄
    - product가 없거나 서보드라이브가 아니면 → model_name이 모터일 가능성을 역검색해서
      호환 서보드라이브 상세 사양 안내
    - 해당사항 없으면 빈 문자열
    """
    if product and product.category == "servo":
        if product.specs and product.specs.extra_specs:
            motor_text = _motor_text(product.specs)
            if motor_text and motor_text != "-":
                return f"\n\n🔩 호환 서보모터: {motor_text}"
        return ""

    # product가 없거나 서보드라이브가 아님 → 모터일 가능성으로 역검색
    drive_detail = await find_drives_compatible_with_motor(model_name, db)
    if drive_detail:
        return f"\n\n{drive_detail}"

    return ""


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

    # 1) 단종여부 + 대체품 + 타사 참고후보 + 호환모터 (find_replacement가 이제
    #    get_servo_companion_note로 호환모터 정보를 자체 포함하므로 여기서 따로 안 붙임)
    #    지연 임포트: replacement.py가 inventory.py를 참조하고 inventory.py가
    #    이 모듈을 참조하는 순환참조를 피하기 위해 함수 내부에서 임포트.
    from app.services.replacement import find_replacement
    replacement_info = await find_replacement(model_name, db)
    sections.append(replacement_info)

    # 2) 타 제조사 동일 용량 비교
    capacity_w = (
        product.specs.extra_specs.get("capacity_w")
        if product.specs and product.specs.extra_specs else None
    )
    if capacity_w:
        comparison = await find_servo_by_capacity(capacity_w, db)
        sections.append(f"🏭 **{capacity_w:g}W 동일 용량 타사 비교**\n{comparison}")

    return "\n\n".join(sections)


async def find_drives_compatible_with_motor(motor_model: str, db: AsyncSession) -> str | None:
    """서보모터 모델명으로 호환되는 서보드라이브를 역으로 찾아 상세 사양까지 안내한다.
    매칭되는 드라이브가 없으면 None 반환 (모터 DB 자체가 없어 호출부에서 일반 조회로 넘어가게 함).
    모터 자체의 외형치수는 신뢰 가능한 출처가 없어 제공하지 않음 — 잘못된 치수 안내가
    감속기 커플링 등 실제 기계적 불일치 사고로 이어질 수 있기 때문."""
    stmt = (
        select(Product, Specification)
        .join(Specification, Specification.product_id == Product.id)
        .where(Product.category == "servo")
    )
    result = await db.execute(stmt)
    rows = result.all()

    motor_key = motor_model.strip().lower()
    matched = []
    for p, s in rows:
        if not s.extra_specs:
            continue
        motors = s.extra_specs.get("compatible_motors", [])
        if any(motor_key in m.lower() or m.lower() in motor_key for m in motors):
            matched.append((p, s))

    if not matched:
        return None

    matched.sort(key=lambda ps: (ps[0].manufacturer, ps[0].model_name))

    detail_blocks = []
    for p, s in matched:
        capacity_w = s.extra_specs.get("capacity_w") if s.extra_specs else None
        capacity_label = f"{capacity_w:g}W" if capacity_w else "-"
        detail_blocks.append(_single_block(p, s, capacity_w) if capacity_w else (
            f"**{p.manufacturer} {p.model_name}**\n"
            f"인터페이스: {s.extra_specs.get('interface_note', '-') if s.extra_specs else '-'}\n"
            f"정격 출력전류: {s.extra_specs.get('rated_output_current_a', '-') if s.extra_specs else '-'}A"
        ))

    header = (
        f"**{motor_model}** 서보모터와 호환 가능한 서보드라이브 {len(matched)}종:"
    )
    body = "\n\n".join(detail_blocks)

    # motor_specs로 실제 치수가 등록된 모터면, 이 뒤에 find_reducer_compat()가
    # 실제 치수를 붙여주므로 "치수 확인 불가" 안내를 붙이면 한 응답 안에서 모순됨 — 생략.
    if _motor_has_registered_specs(motor_model, rows):
        return f"{header}\n\n{body}"

    motor_size_note = (
        "\n\n⚠️ 모터 자체의 외형치수(프레임 사이즈/축 지름 등)는 확인된 카탈로그 도면이 "
        "없어 안내드리기 어렵습니다. 감속기 연결 등 치수가 중요한 작업이시라면, 반드시 "
        "제품 명판이나 제조사 정식 카탈로그로 직접 확인 부탁드립니다. 잘못된 치수 안내로 "
        "커플링 등이 안 맞는 경우가 생길 수 있어 확실하지 않은 수치는 알려드리지 않습니다.\n"
        "☎️ 정확한 치수가 필요하시면 현대자동화(010-3861-2030)로 문의 주세요."
    )

    return f"{header}\n\n{body}{motor_size_note}"


async def _all_servo_rows(db: AsyncSession) -> list[tuple[Product, Specification]]:
    stmt = (
        select(Product, Specification)
        .join(Specification, Specification.product_id == Product.id)
        .where(Product.category == "servo")
    )
    result = await db.execute(stmt)
    return result.all()


async def _all_reducer_rows(db: AsyncSession) -> list[Reducer]:
    result = await db.execute(select(Reducer))
    return list(result.scalars().all())


def _match_reducers_by_bore(shaft_mm: float, reducer_rows: list[Reducer]) -> list[tuple[Reducer, str]]:
    """축경(mm) 기준으로 호환 가능한 감속기를 찾는다.

    단순히 '축경 <= 입력홀'인 행을 전부 반환하지 않는다 — 그러면 14mm 모터에도
    입력홀 <=48mm인 AB220(2000Nm급 대형 감속기)까지 걸려 숫자로는 맞지만 실제로는
    터무니없는 추천이 된다. 대신 이 축경을 수용하는 행들 중 '가장 작은 입력홀 등급'에
    해당하는 행만 반환한다 (동급 여러 시리즈/단수가 동점일 수 있음).
    """
    fits: list[tuple[Reducer, float, str]] = []
    for r in reducer_rows:
        if r.input_bore_std_mm is not None and shaft_mm <= r.input_bore_std_mm:
            fits.append((r, r.input_bore_std_mm, "표준"))
        elif r.input_bore_optional_mm is not None and shaft_mm <= r.input_bore_optional_mm:
            fits.append((r, r.input_bore_optional_mm, "옵션 주문 필요"))

    if not fits:
        return []

    min_bore = min(bore for _, bore, _ in fits)
    matches = [(r, grade) for r, bore, grade in fits if bore == min_bore]
    matches.sort(key=lambda m: (m[0].series, m[0].model_name, m[0].stage))
    return matches


def _format_reducer_matches(shaft_mm: float, matches: list[tuple[Reducer, str]]) -> str:
    lines = [f"축경 {shaft_mm:g}mm 기준 호환 가능 감속기 {len(matches)}건:"]
    for r, grade in matches:
        bore_note = f"≤{r.input_bore_std_mm:g}mm"
        if r.input_bore_optional_mm is not None:
            bore_note += f"(옵션 ≤{r.input_bore_optional_mm:g}mm)"
        lines.append(f"- {r.model_name} ({r.stage}단, 입력홀 {bore_note}) — {grade}")
    return "\n".join(lines)


def _format_motor_spec_block(motor_key: str, motor_data: dict) -> str:
    """motor_specs[모터명] 항목 하나(전기사양+치수+감속기)를 출력 블록으로 포맷."""
    lines = [f"**{motor_key}**"]

    spec_items = []
    if motor_data.get("power_w") is not None:
        spec_items.append(f"정격출력 {motor_data['power_w']}W")
    if motor_data.get("rated_torque_nm") is not None:
        spec_items.append(f"정격토크 {motor_data['rated_torque_nm']}N·m")
    if motor_data.get("max_torque_nm") is not None:
        spec_items.append(f"최대토크 {motor_data['max_torque_nm']}N·m")
    if motor_data.get("rated_speed_rpm") is not None and motor_data.get("max_speed_rpm") is not None:
        spec_items.append(f"속도 {motor_data['rated_speed_rpm']}/{motor_data['max_speed_rpm']}rpm(정격/최대)")
    if motor_data.get("inertia_j") is not None:
        brake = f"({motor_data['inertia_j_brake']})" if motor_data.get("inertia_j_brake") is not None else ""
        spec_items.append(f"관성모멘트 {motor_data['inertia_j']}{brake}×10⁻⁴kg·m²")
    if motor_data.get("mass_kg") is not None:
        brake = f"({motor_data['mass_kg_brake']})" if motor_data.get("mass_kg_brake") is not None else ""
        spec_items.append(f"질량 {motor_data['mass_kg']}{brake}kg")
    if spec_items:
        lines.append(f"전기사양: {' | '.join(spec_items)}")

    dims = motor_data.get("dimensions") or {}
    dim_items = []
    if dims.get("frame_size_mm") is not None:
        dim_items.append(f"플랜지 프레임 □{dims['frame_size_mm']}mm")
    if dims.get("body_size_mm") is not None:
        dim_items.append(f"몸체 단면 □{dims['body_size_mm']}mm")
    length = dims.get("overall_length_mm")
    if length is not None:
        brake = f"({dims['overall_length_mm_brake']})" if dims.get("overall_length_mm_brake") is not None else ""
        dim_items.append(f"전장 {length}{brake}mm")
    if dims.get("shaft_diameter_mm") is not None:
        dim_items.append(f"축경 {dims['shaft_diameter_mm']}mm")
    if dims.get("shaft_length_mm") is not None:
        dim_items.append(f"축길이 {dims['shaft_length_mm']}mm")
    if dims.get("flange_spec"):
        dim_items.append(f"플랜지 볼트 {dims['flange_spec']}")
    if dim_items:
        lines.append(f"치수: {' | '.join(dim_items)}")

    reducers = motor_data.get("reducers") or []
    if reducers:
        reducer_lines = []
        for r in reducers:
            parts = [r.get("model", "-")]
            if r.get("reduction_ratio"):
                parts.append(f"감속비 {r['reduction_ratio']}")
            if r.get("coupling_note"):
                parts.append(r["coupling_note"])
            reducer_lines.append(" · ".join(parts))
        lines.append("결합 가능 감속기:\n" + "\n".join(f"  - {rl}" for rl in reducer_lines))
    else:
        lines.append("결합 가능 감속기: 감속기 호환 정보 미등록")

    return "\n".join(lines)


def _motor_has_registered_specs(motor_model: str, rows: list) -> bool:
    """motor_model이 어느 드라이브에든 motor_specs로 등록돼 있는지 확인.
    (find_drives_compatible_with_motor가 정적 '치수 없음' 안내를 붙일지 판단하는 데 사용)"""
    key = motor_model.strip().lower()
    for _, s in rows:
        motor_specs = (s.extra_specs or {}).get("motor_specs") or {}
        for motor_key in motor_specs:
            if key in motor_key.lower() or motor_key.lower() in key:
                return True
    return False


async def find_reducer_compat(model_name: str, db: AsyncSession) -> str | None:
    """드라이브 모델명 또는 모터 모델명으로 등록된 감속기 결합사양(motor_specs)을 조회.

    - model_name이 드라이브 자체와 매칭되면 그 드라이브의 motor_specs 전체를 반환.
    - 아니면 model_name을 모터명으로 간주해 모든 드라이브의 motor_specs 키를 매칭.
    - motor_specs 데이터가 없으면(=아직 검증된 치수/감속기 정보가 없는 모터) None 반환
      → 호출부가 기존 폴백(일반 스펙 조회 등)으로 넘어가게 함.
    """
    rows = await _all_servo_rows(db)
    key = model_name.strip().lower()

    # 1) 드라이브 자체 모델명/시리즈로 매칭
    for p, s in rows:
        if key in p.model_name.lower() or (p.series and key in p.series.lower()):
            motor_specs = (s.extra_specs or {}).get("motor_specs") or {}
            if motor_specs:
                blocks = [_format_motor_spec_block(m, d) for m, d in motor_specs.items()]
                header = f"**{p.manufacturer} {p.model_name}** 호환 모터 결합사양:"
                return _with_dimension_disclaimer(f"{header}\n\n" + "\n\n".join(blocks))

    # 2) 모터명으로 매칭 (여러 드라이브에 걸쳐 등록돼 있을 수 있음 — 모두 수집)
    matched_blocks = []
    for p, s in rows:
        motor_specs = (s.extra_specs or {}).get("motor_specs") or {}
        for motor_key, motor_data in motor_specs.items():
            if key in motor_key.lower() or motor_key.lower() in key:
                block = _format_motor_spec_block(motor_key, motor_data)
                matched_blocks.append(f"(호환 드라이브: {p.manufacturer} {p.model_name})\n{block}")

    if not matched_blocks:
        return None

    header = f"**{model_name}** 결합사양:"
    return _with_dimension_disclaimer(f"{header}\n\n" + "\n\n".join(matched_blocks))


async def find_motors_by_reducer(reducer_model: str, db: AsyncSession) -> str | None:
    """감속기 모델명으로 호환되는 서보모터(+소속 드라이브)를 역검색.
    매칭되는 감속기 등록이 없으면 None 반환."""
    rows = await _all_servo_rows(db)
    key = reducer_model.strip().lower()

    matches = []  # (drive_product, motor_key, reducer_dict)
    for p, s in rows:
        motor_specs = (s.extra_specs or {}).get("motor_specs") or {}
        for motor_key, motor_data in motor_specs.items():
            for r in motor_data.get("reducers") or []:
                r_model = (r.get("model") or "").lower()
                if r_model and (key in r_model or r_model in key):
                    matches.append((p, motor_key, r))

    if not matches:
        return None

    lines = [f"**{reducer_model}** 감속기와 호환되는 서보모터 {len(matches)}건:"]
    for p, motor_key, r in matches:
        ratio = f", 감속비 {r['reduction_ratio']}" if r.get("reduction_ratio") else ""
        note = f" — {r['coupling_note']}" if r.get("coupling_note") else ""
        lines.append(f"- {motor_key} (드라이브: {p.manufacturer} {p.model_name}){ratio}{note}")

    return _with_dimension_disclaimer("\n".join(lines))