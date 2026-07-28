"""서보드라이브 용량(W) 기반 추천 검색 + 모델별 상세조회 + 모터 역검색
(v9 — 드라이브 상세검색 시 단종/대체품/호환모터/타사비교 통합, 모터 검색시 호환드라이브 역검색 추가,
감속기 결합사양(motor_specs) 양방향 검색 추가)"""
import re

from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Product, Specification, Replacement, ProductStatus, Reducer

_KNOWN_CAPACITIES_W = [50, 100, 200, 400, 500, 600, 750, 1000, 1500, 2000, 3000, 3500, 5000, 6000, 7000, 7500, 11000, 15000]

# ─── J4 시리즈 기준 사이즈 테이블 (용량W → 형명(KR/MR)/플랜지프레임/서보드라이브A·B).
# J2S 등 타 시리즈 모터가 motor_specs에 실측 등록돼 있지 않을 때 "동일 용량이면 동일
# 프레임" 규칙으로 사이즈를 유추하는 폴백 근거로만 쓴다 — 실측 motor_specs 항목이 있으면
# 항상 그쪽이 우선(find_reducer_compat 참조). register_j4_motor_sizes.py가 이 표를 그대로
# import해서 MR-J4-xxA/xxB의 motor_specs에도 등록해, HG-KR/HG-MR 자체 조회도 실측
# 경로로 답한다. 신규 시리즈(J5 등) 추가 시에도 사이즈 기준표는 반드시 여기 한 곳에만
# 정의하고, 등록 스크립트는 값을 복사하지 말고 import해서 쓸 것 — 두 곳에 따로 정의하면
# 조용히 어긋나는 데이터 불일치가 재발한다. ───
J4_SIZE_TABLE = [
    {"capacity_w": 50, "hg_kr": "HG-KR053", "hg_mr": "HG-MR053", "frame_mm": 40, "drive_a": "MR-J4-10A", "drive_b": "MR-J4-10B"},
    {"capacity_w": 100, "hg_kr": "HG-KR13", "hg_mr": "HG-MR13", "frame_mm": 40, "drive_a": "MR-J4-10A", "drive_b": "MR-J4-10B"},
    {"capacity_w": 200, "hg_kr": "HG-KR23", "hg_mr": "HG-MR23", "frame_mm": 60, "drive_a": "MR-J4-20A", "drive_b": "MR-J4-20B"},
    {"capacity_w": 400, "hg_kr": "HG-KR43", "hg_mr": "HG-MR43", "frame_mm": 60, "drive_a": "MR-J4-40A", "drive_b": "MR-J4-40B"},
    {"capacity_w": 750, "hg_kr": "HG-KR73", "hg_mr": "HG-MR73", "frame_mm": 80, "drive_a": "MR-J4-70A", "drive_b": "MR-J4-70B"},
    {"capacity_w": 500, "hg_kr": "HG-SR52", "hg_mr": "HG-MR52", "frame_mm": 130, "drive_a": "MR-J4-60A", "drive_b": "MR-J4-60B"},
    {"capacity_w": 1000, "hg_kr": "HG-SR102", "hg_mr": "HG-MR102", "frame_mm": 130, "drive_a": "MR-J4-100A", "drive_b": "MR-J4-100B"},
    {"capacity_w": 1500, "hg_kr": "HG-SR152", "hg_mr": "HG-MR152", "frame_mm": 130, "drive_a": "MR-J4-200A", "drive_b": "MR-J4-200B"},
    {"capacity_w": 2000, "hg_kr": "HG-SR202", "hg_mr": "HG-MR202", "frame_mm": 176, "drive_a": "MR-J4-200A", "drive_b": "MR-J4-200B"},
    {"capacity_w": 3500, "hg_kr": "HG-SR352", "hg_mr": "HG-MR352", "frame_mm": 176, "drive_a": "MR-J4-350A", "drive_b": "MR-J4-350B"},
    {"capacity_w": 5000, "hg_kr": "HG-SR502", "hg_mr": "HG-MR502", "frame_mm": 176, "drive_a": "MR-J4-500A", "drive_b": "MR-J4-500B"},
    {"capacity_w": 7000, "hg_kr": "HG-SR702", "hg_mr": "HG-MR702", "frame_mm": 176, "drive_a": "MR-J4-700A", "drive_b": "MR-J4-700B"},
]
_J4_SIZE_BY_CAPACITY: dict[float, dict] = {row["capacity_w"]: row for row in J4_SIZE_TABLE}

# ─── 모터 물리치수/감속기 결합사양 안내 시 항상 첨부하는 면책 문구 (단일 관리 지점).
# 치수가 등록된 경우/미등록인 경우 모두 이 문구 하나로 통일해서 끝에 붙인다 —
# 두 경로가 서로 다른 문구를 쓰면 같은 챗봇인데 안내가 다르다는 혼동을 준다. ───
_DIMENSION_DISCLAIMER = (
    "\n\n⚠️ 위 치수 및 결합 사양은 참고용이며, 최종 확인은 반드시 제조사 정식 매뉴얼/도면을 "
    "참고하시고 실제 장착 전 확인 부탁드립니다. 치수 오류로 인한 기계적 손상은 책임지지 않습니다.\n"
    "☎️ 정확한 확인이 필요하시면 현대자동화(010-3861-2030)로 문의 주세요."
)


def _with_dimension_disclaimer(text: str) -> str:
    return text + _DIMENSION_DISCLAIMER


# ─── 감속기 자동매칭 결과에 항상 첨부하는 어댑터 확인 문구 (치수 disclaimer와 별개) ───
_REDUCER_ADAPTER_DISCLAIMER = (
    "\n\n🔩 정확한 모터 장착 어댑터(C1~C10)는 APEX 측 확인이 필요합니다."
)


_MOTOR_NAME_PREFIXES = ("HG-", "HC-", "HA-")
_BRAKE_SAME_SIZE_NOTE = "브레이크 내장 모델로 사이즈는 동일합니다."


def _is_motor_model_name(name: str) -> bool:
    """HG-/HC-/HA-로 시작하면 서보'모터' 형명, MR-로 시작하면 서보'드라이브' 형명.
    드라이브 쪽 B(예: MR-J4-70B)는 SSCNET 인터페이스를 뜻하므로 브레이크 판단 대상에서
    제외해야 한다 — 호출측은 이 함수로 먼저 걸러낸 뒤에만 _split_brake_suffix를 쓴다."""
    return name.strip().upper().startswith(_MOTOR_NAME_PREFIXES)


def _split_brake_suffix(motor_name: str) -> tuple[str, bool]:
    """모터 형명 끝의 브레이크 내장 접미사 'B'를 분리한다.
    숫자 바로 뒤에 오는 B만 브레이크로 간주한다(예: HC-KFS43B -> ("HC-KFS43", True)).
    'M' 등 다른 접미사나, 원래 숫자로 끝나는 형명은 그대로 반환한다."""
    stripped = motor_name.strip()
    if len(stripped) >= 2 and stripped[-1].upper() == "B" and stripped[-2].isdigit():
        return stripped[:-1], True
    return stripped, False


def _drive_family_key(model_name: str) -> str:
    """MR-J2S-10A/10A1처럼 '용량코드+인터페이스문자+단상 변형 접미 1' 패턴으로 등록된
    모델명에서 단상(100-120V) 변형 접미사 '1'을 제거해 동일 계열 키를 만든다.
    표시되는 스펙(무게/인터페이스/호환모터)이 사실상 동일한 A/A1, B/B1 쌍만 묶고,
    실제로 인터페이스가 다른 A 계열과 B 계열은 구분되게 유지한다."""
    m = re.match(r"^(.*\d[A-Za-z])1$", model_name)
    return m.group(1) if m else model_name


def _dedupe_drive_pairs_by_family(pairs: list) -> list:
    """동일 계열(단상/삼상 변형만 다른) 드라이브 중복을 제거하고 계열당 1건만 남긴다.
    먼저 나온 항목(정렬 후 대표값)을 계열 대표로 남긴다."""
    seen_families: set[str] = set()
    deduped = []
    for p, s in pairs:
        family = _drive_family_key(p.model_name)
        if family in seen_families:
            continue
        seen_families.add(family)
        deduped.append((p, s))
    return deduped


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
    current_a = s.extra_specs.get("rated_output_current_a")

    lines = [
        f"**{p.manufacturer} {p.model_name}** ({capacity_w:g}W{weight})",
        f"인터페이스: {interface}",
    ]
    # rated_output_current_a는 일부 등록 스크립트(MR-J2S/LS)가 매뉴얼에서 애초에
    # 추출하지 않은 값이라 항상 비어있다 — "-A" 자리채움 대신 줄 자체를 생략.
    if current_a is not None:
        lines.append(f"정격 출력전류: {current_a}A")
    lines.append(f"호환 서보모터: {_motor_text(s)}")

    block = "\n".join(lines)
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
    ]
    # 비교 대상 중 하나라도 정격 출력전류가 등록돼 있으면 표시(미등록 모델은 "-"로
    # 대비 표시하는 게 유용) — 전부 미등록이면 "-"만 나열된 무의미한 섹션이므로 생략.
    if any(v != "-" for v in current_vals):
        parts.append(section("정격 출력전류", current_vals))
    parts += [
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
    replacement_info, _ = await find_replacement(model_name, db)
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
    matched = _dedupe_drive_pairs_by_family(matched)

    detail_blocks = []
    for p, s in matched:
        capacity_w = s.extra_specs.get("capacity_w") if s.extra_specs else None
        if capacity_w:
            detail_blocks.append(_single_block(p, s, capacity_w))
            continue

        fallback_lines = [
            f"**{p.manufacturer} {p.model_name}**",
            f"인터페이스: {s.extra_specs.get('interface_note', '-') if s.extra_specs else '-'}",
        ]
        current_a = s.extra_specs.get("rated_output_current_a") if s.extra_specs else None
        if current_a is not None:
            fallback_lines.append(f"정격 출력전류: {current_a}A")
        detail_blocks.append("\n".join(fallback_lines))

    header = (
        f"**{motor_model}** 서보모터와 호환 가능한 서보드라이브 {len(matched)}종:"
    )
    body = "\n\n".join(detail_blocks)

    # motor_specs로 실제 치수가 등록된 모터면, 이 뒤에 find_reducer_compat()가
    # 실제 치수를 붙여주므로 "치수 확인 불가" 안내를 붙이면 한 응답 안에서 모순됨 — 생략.
    if _motor_has_registered_specs(motor_model, rows):
        return f"{header}\n\n{body}"

    # 마감 문구(참고용 안내 + 문의 전화번호)는 _DIMENSION_DISCLAIMER 하나로 통일해서 붙인다.
    # 치수 등록 케이스(위 return)는 find_reducer_compat()가 같은 disclaimer를 붙이므로
    # 여기서 또 붙이면 한 응답에 중복 — 이 분기(미등록)에서만 여기서 붙인다.
    motor_size_note = (
        "\n\n⚠️ 모터 자체의 외형치수(프레임 사이즈/축 지름 등)는 확인된 카탈로그 도면이 "
        "없어 안내드리기 어렵습니다. 감속기 연결 등 치수가 중요한 작업이시라면, 반드시 "
        "제품 명판이나 제조사 정식 카탈로그로 직접 확인 부탁드립니다. 잘못된 치수 안내로 "
        "커플링 등이 안 맞는 경우가 생길 수 있어 확실하지 않은 수치는 알려드리지 않습니다."
    )

    return _with_dimension_disclaimer(f"{header}\n\n{body}{motor_size_note}")


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


def _format_motor_spec_block(motor_key: str, motor_data: dict, reducer_rows: list[Reducer]) -> tuple[str, bool]:
    """motor_specs[모터명] 항목 하나(전기사양+치수+감속기)를 출력 블록으로 포맷.

    반환값: (블록 텍스트, 🔩 어댑터 확인 문구가 필요한지). 문구를 여기서 바로 붙이면
    드라이브 하나에 자동매칭된 모터가 여러 개일 때 문구가 모터 개수만큼 반복
    노출된다 — 호출부가 전체 응답 조립 후 단 한 번만 붙이도록 플래그만 반환한다
    (치수 disclaimer(_with_dimension_disclaimer)와 동일 패턴, 코드리뷰 H8).
    """
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

    # 감속기 섹션 출력 기준(정책):
    # - motor_data에 큐레이션된 reducers 목록이 있으면 그대로 출력.
    # - 없지만 축경(shaft_diameter_mm)이 있으면 AB/ABR 카탈로그로 자동매칭 시도
    #   (매칭 0건이어도 "조회는 했으나 안 맞음"은 정보성이 있으므로 그대로 출력).
    # - 축경조차 없으면 판단 근거 자체가 없는 것이므로 "미등록" 같은 자리채움 문구를
    #   내지 않고 감속기 섹션을 통째로 생략한다.
    needs_adapter_disclaimer = False
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
        shaft_mm = dims.get("shaft_diameter_mm")
        if shaft_mm is not None:
            auto_matches = _match_reducers_by_bore(shaft_mm, reducer_rows)
            if auto_matches:
                lines.append(_format_reducer_matches(shaft_mm, auto_matches))
                needs_adapter_disclaimer = True
            else:
                lines.append(
                    "결합 가능 감속기: AB/ABR 라인업 내 호환 모델 없음 "
                    "(다른 감속기 시리즈 또는 커스텀 확인 필요)"
                )
        # else: 축경 미등록 = 감속기 호환 여부를 판단할 근거가 없음 → 섹션 생략

    return "\n".join(lines), needs_adapter_disclaimer


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


def _find_capacity_w_by_compatible_motor(base_motor_name: str, rows: list) -> float | None:
    """base_motor_name이 어느 드라이브의 compatible_motors에 정확히(대소문자 무시) 등록돼
    있는지 찾아 그 드라이브의 capacity_w를 반환한다. 부분일치가 아닌 완전일치만 쓴다 —
    이 값이 J4 사이즈 유추의 근거가 되므로, 느슨한 매칭으로 엉뚱한 용량을 끌어오면 안 된다."""
    key = base_motor_name.strip().lower()
    for _, s in rows:
        if not s.extra_specs:
            continue
        motors = s.extra_specs.get("compatible_motors", [])
        if any(key == m.strip().lower() for m in motors):
            return s.extra_specs.get("capacity_w")
    return None


def _j2s_to_j4_size_note(model_name: str, rows: list) -> str | None:
    """J2S 등 타 시리즈 서보모터가 motor_specs에 실측 등록돼 있지 않을 때, 같은 용량의
    J4 시리즈 모터와 플랜지 프레임 사이즈가 동일하다는 규칙으로 사이즈+대응 서보드라이브를
    유추해 안내 문구를 만든다. find_reducer_compat()가 실측 motor_specs 매칭에 모두
    실패한 뒤에만 호출하는 순수 폴백이다. 매칭 근거(같은 용량의 J4 표 행)가 없으면
    None을 반환해 "찾지 못함" 상태를 그대로 유지한다(추측으로 채우지 않음)."""
    if not _is_motor_model_name(model_name):
        return None

    base, has_brake = _split_brake_suffix(model_name)

    if base.upper().startswith("HG-"):
        return None  # J4 계열 자체 모델은 실측 등록 대상 — 이 폴백을 타지 않는다.

    capacity_w = _find_capacity_w_by_compatible_motor(base, rows)
    if capacity_w is None:
        return None

    j4_row = _J4_SIZE_BY_CAPACITY.get(capacity_w)
    if j4_row is None:
        return None

    lines = [
        f"J2S 시리즈이지만 J4 시리즈의 {j4_row['hg_kr']}({capacity_w:g}W) 모델과 사이즈가 "
        f"동일하여 해당 값을 기준으로 안내드립니다.",
        f"플랜지 프레임: □{j4_row['frame_mm']}mm",
        f"대응 서보드라이브: {j4_row['drive_a']}, {j4_row['drive_b']}",
    ]
    if has_brake:
        lines.append(_BRAKE_SAME_SIZE_NOTE)

    return "\n".join(lines)


async def find_reducer_compat(model_name: str, db: AsyncSession) -> str | None:
    """드라이브 모델명 또는 모터 모델명으로 등록된 감속기 결합사양(motor_specs)을 조회.

    - model_name이 드라이브 자체와 매칭되면 그 드라이브의 motor_specs 전체를 반환.
    - 아니면 model_name을 모터명으로 간주해 모든 드라이브의 motor_specs 키를 매칭.
    - motor_specs 데이터가 없으면(=아직 검증된 치수/감속기 정보가 없는 모터) None 반환
      → 호출부가 기존 폴백(일반 스펙 조회 등)으로 넘어가게 함.
    """
    rows = await _all_servo_rows(db)
    reducer_rows = await _all_reducer_rows(db)
    key = model_name.strip().lower()

    # 1) 드라이브 자체 모델명/시리즈로 매칭
    for p, s in rows:
        if key in p.model_name.lower() or (p.series and key in p.series.lower()):
            motor_specs = (s.extra_specs or {}).get("motor_specs") or {}
            if motor_specs:
                block_results = [_format_motor_spec_block(m, d, reducer_rows) for m, d in motor_specs.items()]
                blocks = [b for b, _ in block_results]
                needs_adapter_disclaimer = any(flag for _, flag in block_results)
                header = f"**{p.manufacturer} {p.model_name}** 호환 모터 결합사양:"
                body = f"{header}\n\n" + "\n\n".join(blocks)
                if needs_adapter_disclaimer:
                    body += _REDUCER_ADAPTER_DISCLAIMER
                return _with_dimension_disclaimer(body)

    # 2) 모터명으로 매칭 (여러 드라이브에 걸쳐 등록돼 있을 수 있음 — 모두 수집)
    _, query_has_brake = (
        _split_brake_suffix(model_name) if _is_motor_model_name(model_name) else (model_name, False)
    )
    matched_blocks = []
    needs_adapter_disclaimer = False
    seen_families: set[str] = set()
    for p, s in rows:
        motor_specs = (s.extra_specs or {}).get("motor_specs") or {}
        for motor_key, motor_data in motor_specs.items():
            if key in motor_key.lower() or motor_key.lower() in key:
                family = _drive_family_key(p.model_name)
                if family in seen_families:
                    continue
                seen_families.add(family)
                block, flag = _format_motor_spec_block(motor_key, motor_data, reducer_rows)
                needs_adapter_disclaimer = needs_adapter_disclaimer or flag
                if query_has_brake:
                    block += f"\n※ {_BRAKE_SAME_SIZE_NOTE}"
                matched_blocks.append(f"(호환 드라이브: {p.manufacturer} {p.model_name})\n{block}")

    if matched_blocks:
        header = f"**{model_name}** 결합사양:"
        body = f"{header}\n\n" + "\n\n".join(matched_blocks)
        if needs_adapter_disclaimer:
            body += _REDUCER_ADAPTER_DISCLAIMER
        return _with_dimension_disclaimer(body)

    # 3) 실측 motor_specs가 전혀 없는 J2S 등 타 시리즈 모터 -> J4 동일 용량 사이즈 유추 폴백
    j2s_note = _j2s_to_j4_size_note(model_name, rows)
    if j2s_note:
        header = f"**{model_name}** 결합사양(J4 시리즈 기준 유추):"
        return _with_dimension_disclaimer(f"{header}\n\n{j2s_note}")

    return None


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