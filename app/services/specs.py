from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Product, Specification
from app.core.clova import clova_client, SYSTEM_PROMPTS


async def lookup_specs(model_name: str, db: AsyncSession) -> str:
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
    products = result.scalars().all()

    if not products:
        return (
            f"'{model_name}' 모델의 스펙 정보를 찾지 못했습니다.\n"
            f"정확한 모델명을 확인하시거나 현대자동화로 문의해 주세요."
        )

    context = _build_context(products)
    response = await clova_client.chat_completion(
        system_prompt=SYSTEM_PROMPTS["specs"],
        user_message=(
            f"[검색 결과]\n{context}\n\n"
            f"[질문]\n'{model_name}' 제품의 규격과 사이즈를 알려주세요."
        ),
        temperature=0.1,
    )
    return response


def _build_context(products: list) -> str:
    lines = []
    for p in products:
        lines.append(f"[제품] {p.model_name}")
        lines.append(f"제조사: {p.manufacturer} | 시리즈: {p.series} | 카테고리: {p.category}")
        if p.specs:
            s = p.specs
            if s.dimension_w:
                lines.append(f"외형(WxHxD): {s.dimension_w}x{s.dimension_h}x{s.dimension_d}mm")
            if s.weight_kg: lines.append(f"중량: {s.weight_kg}kg")
            if s.input_voltage: lines.append(f"전원: {s.input_voltage}")
            if s.output_type: lines.append(f"출력방식: {s.output_type}")
            if s.io_points: lines.append(f"입출력: {s.io_points}")
            if s.comm_protocol: lines.append(f"통신: {s.comm_protocol}")
            if s.operating_temp: lines.append(f"동작온도: {s.operating_temp}")
            if s.protection_class: lines.append(f"보호등급: {s.protection_class}")
            if s.rated_power: lines.append(f"정격출력: {s.rated_power}")
            if s.drawing_url: lines.append(f"도면링크: {s.drawing_url}")
        else:
            lines.append("(스펙 미등록 - 판매자 문의)")
        lines.append("")
    return "\n".join(lines)
