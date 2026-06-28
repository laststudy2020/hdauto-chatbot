from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Product, Replacement, Specification, ProductStatus
from app.core.clova import clova_client, SYSTEM_PROMPTS
from app.services.inventory import get_stock_state, COMPANY_PHONE
from app.services.servo_spec_search import get_servo_companion_note


async def find_replacement(model_name: str, db: AsyncSession) -> str:
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
            )
        return (
            f"'{model_name}' 모델 정보를 찾지 못했습니다.\n"
            f"정확한 모델명을 확인하시거나 현대자동화로 문의해 주세요."
        )

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
            )

        if stock["state"] == "low_stock":
            return (
                f"'{product.model_name}'은(는) 현재 정상 판매 중이며, "
                f"재고 {stock['quantity']}개 남았습니다 (소진 임박).\n"
                f"제조사: {product.manufacturer} | 시리즈: {product.series}{spec_info}"
                f"{companion_note}"
            )

        return (
            f"'{product.model_name}'은(는) 현재 정상 판매 중입니다.\n"
            f"제조사: {product.manufacturer} | 시리즈: {product.series}"
            f"{spec_info}{companion_note}"
        )

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

    if not replacements:
        # 정식 대체품은 없어도 타사 참고 후보는 있을 수 있으므로 먼저 확인
        cross_brand_note = _build_cross_brand_note(product)
        companion_note = await get_servo_companion_note(product, model_name, db)
        base = (
            f"'{product.model_name}'은(는) 단종 제품이지만\n"
            f"등록된 대체품 정보가 없습니다. 현대자동화로 문의해 주세요."
        )
        return base + cross_brand_note + companion_note

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

    # 5) 타사 참고 후보 — 검증 안 된 참고정보이므로 AI 패러프레이즈 없이
    #    고정 문구로 별도 섹션에 덧붙인다 (확정 대체품과 절대 혼동되지 않도록).
    response += _build_cross_brand_note(product)
    response += await get_servo_companion_note(product, model_name, db)

    return response


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
        lines.append(f"단자대 호환: {'O' if rep.terminal_compatible else 'X'}")
        lines.append(f"프로그램 변환: {'가능' if rep.program_convertible else '필요'}")
        lines.append(f"외형 호환: {'O' if rep.dimension_compatible else 'X'}")
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