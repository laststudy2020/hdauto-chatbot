import re

from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Product, Replacement, Specification, ProductStatus
from app.core.clova import clova_client, SYSTEM_PROMPTS
from app.services.inventory import get_stock_state, COMPANY_PHONE, STORE_URL
from app.services.servo_spec_search import get_servo_companion_note


async def find_replacement(model_name: str, db: AsyncSession) -> tuple[str, bool]:
    """(응답 텍스트, DB에서 실제로 매칭됐는지) 튜플 반환.

    matched는 응답 문자열 내용과 무관하게 "DB 검색이 뭔가를 찾았는가"만 나타낸다.
    호출측(chatbot._route)이 문자열에 '찾지 못했습니다'가 포함됐는지로 폴백 여부를
    판단하면, CLOVA가 생성한 자유서술 답변에 그 문구가 우연히 섞였을 때 정상적으로
    찾은 DB 답변이 웹검색으로 잘못 덮어써질 수 있다(코드리뷰 H6).
    """
    # 1) 모델명으로 제품 검색 (부분 일치)
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

    if not product:
        # 정식 Product 행이 없어도 서보모터일 가능성이 있음 — 드라이브쪽 호환모터
        # 리스트에서 역검색해서 호환 드라이브를 안내 (모터는 카탈로그에 단독 등록 안 함)
        companion_note = await get_servo_companion_note(None, model_name, db)
        if companion_note:
            return (
                f"'{model_name}'은(는) 당사 카탈로그에 별도 등록된 모델은 아니지만, "
                f"서보모터로 확인됩니다.{companion_note}\n\n"
                f"📞 정확한 재고/사양은 현대자동화로 문의해 주세요.\n"
                f"☎️ {COMPANY_PHONE}"
            ), True
        return (
            f"'{model_name}' 모델 정보를 찾지 못했습니다.\n"
            f"정확한 모델명을 확인하시거나 현대자동화로 문의해 주세요."
        ), False

    # 2) 현재 판매 중인 제품 → 재고 상태까지 같이 확인해서 답변
    #    (단종된 건 아니라도 일시 품절/소진임박일 수 있어, "정상 판매중"이라고만 하면
    #     STOCK 의도로 물었을 때와 다른 답을 줄 수 있음 — get_stock_state로 통일)
    if product.status == ProductStatus.ACTIVE:
        stock = await get_stock_state(product, db)
        companion_note = await get_servo_companion_note(product, model_name, db)

        spec_info = ""
        if product.specs:
            s = product.specs
            parts = []
            if s.input_voltage: parts.append(f"전원: {s.input_voltage}")
            if s.io_points: parts.append(f"입출력: {s.io_points}")
            if s.dimension_w:
                parts.append(f"외형: {s.dimension_w}x{s.dimension_h}x{s.dimension_d}mm")
            if parts:
                spec_info = "\n" + " | ".join(parts)

        if stock["state"] == "out_of_stock":
            return (
                f"'{product.model_name}'은(는) 단종된 제품은 아니지만, 현재 일시 재고 없음 상태입니다.\n"
                f"제조사: {product.manufacturer} | 시리즈: {product.series}{spec_info}"
                f"{companion_note}\n\n"
                f"📞 입고 일정은 현대자동화로 문의해 주세요.\n"
                f"☎️ {COMPANY_PHONE}"
            ), True

        if stock["state"] == "low_stock":
            return (
                f"'{product.model_name}'은(는) 현재 정상 판매 중이며, "
                f"재고 {stock['quantity']}개 남았습니다 (소진 임박).\n"
                f"제조사: {product.manufacturer} | 시리즈: {product.series}{spec_info}"
                f"{companion_note}"
            ), True

        return (
            f"'{product.model_name}'은(는) 현재 정상 판매 중입니다.\n"
            f"제조사: {product.manufacturer} | 시리즈: {product.series}"
            f"{spec_info}{companion_note}"
        ), True

    # 3) 단종 → 대체품 검색
    stmt2 = (
        select(Replacement)
        .options(
            selectinload(Replacement.new_product).selectinload(Product.specs)
        )
        .where(Replacement.old_model_id == product.id)
    )
    result2 = await db.execute(stmt2)
    replacements = result2.scalars().all()

    # 단종이어도 보유 재고가 있으면 그걸 먼저 확인한다(아래 두 분기에서 같이 쓴다).
    stock = await get_stock_state(product, db)
    stock_note = _build_stock_note(product, stock)

    if not replacements:
        # 정식 대체품은 없어도 타사 참고 후보는 있을 수 있으므로 먼저 확인
        cross_brand_note = _build_cross_brand_note(product)
        companion_note = await get_servo_companion_note(product, model_name, db)
        base = (
            f"'{product.model_name}'은(는) 단종 제품이지만\n"
            f"등록된 대체품 정보가 없습니다. 현대자동화로 문의해 주세요."
        )
        return base + stock_note + cross_brand_note + companion_note, True

    # 4) RAG 컨텍스트 구성 후 HyperCLOVA 답변 생성
    context = _build_context(product, replacements)
    response = await clova_client.chat_completion(
        system_prompt=SYSTEM_PROMPTS["replacement"],
        user_message=(
            f"[검색 결과]\n{context}\n\n"
            f"[질문]\n'{model_name}' 단종품의 대체품을 알려주세요."
        ),
        temperature=0.2,
    )

    # 5) 정확한 형명·보유 재고·타사 참고 후보 — 틀리면 안 되는 값이거나 검증 안 된
    #    참고정보이므로 AI 패러프레이즈 없이 고정 문구로 별도 섹션에 덧붙인다
    #    (확정 대체품과 절대 혼동되지 않도록).
    response += _build_exact_model_note(replacements)
    response += stock_note
    response += _build_cross_brand_note(product)
    response += await get_servo_companion_note(product, model_name, db)

    return response, True


_LS_INVERTER_SUFFIX = re.compile(r"^(?:LSLV|SV).*-([124])$", re.IGNORECASE)
_SUFFIX_MEANING = {"1": "단상 200V", "2": "3상 200V", "4": "3상 400V"}


def _build_exact_model_note(replacements: list) -> str:
    """확정 대체품의 형명을 LLM 밖에서 고정 문구로 다시 못박는다.

    CLOVA가 형명을 시리즈명으로 뭉개는 일이 비결정적으로 발생한다(프로덕션 실측:
    같은 프롬프트 구조인데 'LSLV0022G100-4'는 유지되고 'LSLV0004S100-1'은
    'LSLV-S100 시리즈'로 소실). 형명 끝자리는 전압·상 구분이라, 시리즈명만 들은
    고객이 단상 전원에 3상 인버터를 사는 사고로 이어진다. 재고 수량·타사 후보와
    같은 부류 — 틀리면 안 되는 값은 생성에 맡기지 않는다.
    """
    models = [r.new_product.model_name for r in replacements if r.new_product]
    if not models:
        return ""

    lines = ["\n\n---", "✅ **정확한 대체 형명**: " + ", ".join(models)]

    # 접미사 의미는 LS 인버터 형명일 때만 밝힌다. 다른 제품군('MR-J4-10A1' 등)은
    # 끝자리 규칙이 달라서 같은 설명을 붙이면 없는 규칙을 지어내는 셈이 된다.
    suffixes = {
        m.group(1) for name in models if (m := _LS_INVERTER_SUFFIX.match(name))
    }
    if suffixes:
        meanings = " / ".join(
            f"-{s} {_SUFFIX_MEANING[s]}" for s in sorted(suffixes)
        )
        lines.append(
            f"형명 끝자리는 전압·상 구분입니다({meanings}). "
            f"주문 시 위 형명 그대로 확인해 주세요."
        )
    return "\n".join(lines)


def _build_stock_note(product: Product, stock: dict) -> str:
    """단종품이라도 우리가 실제로 갖고 파는 재고를 알린다 (없으면 빈 문자열).

    단종 표기를 켜면 대체품 분기가 열리는 대신 "단종입니다, 후속은 X입니다"로 끝나
    정작 우리가 보유한 재고를 안 팔게 된다. iG5A 22건이 스마트스토어에 올라가 있는
    상황이라 실제 매출이 걸린 문제다.

    '중고'라고 말할지는 데이터로만 정한다. "단종인데 재고가 있으면 중고"라고 코드가
    단정하면 신품 데드스톡을 중고로 잘못 안내한다 — 가격·품질 기대가 달라지는 값이라
    조용히 틀리면 안 된다. extra_specs.stock_condition이 'used'일 때만 중고라 밝히고,
    없으면 신품/중고를 주장하지 않는 중립 표현으로 둔다.
    """
    if stock.get("state") not in ("in_stock", "low_stock"):
        return ""
    quantity = stock.get("quantity") or 0
    if quantity <= 0:
        return ""

    condition = ""
    if product.specs and product.specs.extra_specs:
        condition = product.specs.extra_specs.get("stock_condition") or ""

    label = "중고 재고" if condition == "used" else "재고"
    lines = [
        "\n\n---",
        f"📦 다만 당사에 **{product.model_name} {label} {quantity}개**가 있어 "
        f"바로 구매 가능합니다.",
    ]
    if condition == "used":
        lines.append("(신품은 단종됐고, 보유분은 중고입니다. 상태는 문의해 주세요.)")
    if stock.get("state") == "low_stock":
        lines.append("소진 임박 — 서두르시는 걸 권장드립니다.")
    lines.append(f"🛒 {STORE_URL}")
    return "\n".join(lines)


def _build_cross_brand_note(product: Product) -> str:
    """검증되지 않은 타사 참고 후보를 고정 포맷으로 반환 (없으면 빈 문자열)"""
    if not product.specs or not product.specs.extra_specs:
        return ""
    alternatives = product.specs.extra_specs.get("cross_brand_alternatives")
    if not alternatives:
        return ""

    lines = [
        "\n\n---",
        "📌 **타사 참고 후보** (당사가 사양을 검증한 정식 대체품이 아니며, "
        "동일 용량대의 참고 정보입니다. 실제 적용 전 사양 확인이 필요합니다.)",
    ]
    for alt in alternatives:
        manufacturer = alt.get("manufacturer", "미확인")
        model = alt.get("model", "미확인")
        note = alt.get("note", "사양 확인 필요")
        lines.append(f"- {manufacturer} {model} ({note})")
    return "\n".join(lines)


def _flag(value: bool | None, yes: str, no: str) -> str:
    """3상태 렌더링 — None은 검증되지 않았다는 뜻이므로 '미확인'."""
    if value is None:
        return "미확인"
    return yes if value else no


def _build_context(product: Product, replacements: list) -> str:
    lines = [
        f"[단종 제품] {product.model_name}",
        f"제조사: {product.manufacturer} | 시리즈: {product.series}",
        f"카테고리: {product.category}",
        f"단종일: {product.discontinued_date or '미확인'}",
        "",
    ]
    for i, rep in enumerate(replacements, 1):
        new = rep.new_product
        lines.append(f"[대체 모델 {i}] {new.model_name}")
        lines.append(f"제조사: {new.manufacturer} | 시리즈: {new.series}")
        if new.specs:
            s = new.specs
            if s.input_voltage: lines.append(f"전원: {s.input_voltage}")
            if s.io_points: lines.append(f"입출력: {s.io_points}")
            if s.dimension_w:
                lines.append(f"외형: {s.dimension_w}x{s.dimension_h}x{s.dimension_d}mm")
        # NULL은 '미확인'이지 '비호환'이 아니다. 사양 일치만 보고 자동 생성한 매핑은
        # 호환 플래그가 비어 있는데, 이걸 X로 찍으면 검증한 적 없는 비호환을 고객에게
        # 단정하게 된다(반대로 O로 찍으면 없는 호환을 지어낸다).
        lines.append(f"단자대 호환: {_flag(rep.terminal_compatible, 'O', 'X')}")
        lines.append(f"프로그램 변환: {_flag(rep.program_convertible, '가능', '필요')}")
        lines.append(f"외형 호환: {_flag(rep.dimension_compatible, 'O', 'X')}")
        if rep.compatibility_notes:
            lines.append(f"비고: {rep.compatibility_notes}")
        lines.append("")
    return "\n".join(lines)


async def register_replacement(
    old_model: str,
    new_model: str,
    notes: str | None,
    db: AsyncSession,
) -> str:
    """채팅 명령어로 단종→대체품 매핑을 등록 (없는 모델명은 새로 생성)"""
    old_product = await _get_or_create_product(old_model, db, mark_discontinued=True)
    new_product = await _get_or_create_product(new_model, db, mark_discontinued=False)

    stmt = select(Replacement).where(
        Replacement.old_model_id == old_product.id,
        Replacement.new_model_id == new_product.id,
    )
    result = await db.execute(stmt)
    existing = result.scalars().first()

    if existing:
        if notes:
            existing.compatibility_notes = notes
            await db.commit()
            return f"기존 매핑을 갱신했습니다: {old_product.model_name} → {new_product.model_name}"
        return f"이미 등록된 매핑입니다: {old_product.model_name} → {new_product.model_name}"

    replacement = Replacement(
        old_model_id=old_product.id,
        new_model_id=new_product.id,
        compatibility_notes=notes,
    )
    db.add(replacement)
    await db.commit()
    return f"등록 완료: {old_product.model_name} → {new_product.model_name}"


async def _get_or_create_product(
    model_name: str, db: AsyncSession, mark_discontinued: bool
) -> Product:
    stmt = select(Product).where(Product.model_name == model_name)
    result = await db.execute(stmt)
    product = result.scalars().first()

    if product:
        if mark_discontinued and product.status != ProductStatus.DISCONTINUED:
            product.status = ProductStatus.DISCONTINUED
            await db.commit()
        return product

    product = Product(
        model_name=model_name,
        manufacturer="미확인",
        category="미확인",
        status=ProductStatus.DISCONTINUED if mark_discontinued else ProductStatus.ACTIVE,
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return product