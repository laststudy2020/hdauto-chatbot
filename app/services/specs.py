from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Product, Specification
from app.core.clova import clova_client, SYSTEM_PROMPTS


async def lookup_specs(model_name: str, db: AsyncSession) -> tuple[str, bool]:
    """(응답 텍스트, DB에서 실제로 매칭됐는지) 튜플 반환 — 근거는 find_replacement 참조(H6)."""
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
        ), False

    context = _build_context(products)
    response = await clova_client.chat_completion(
        system_prompt=SYSTEM_PROMPTS["specs"],
        user_message=(
            f"[검색 결과]\n{context}\n\n"
            f"[질문]\n'{model_name}' 제품의 규격과 사이즈를 알려주세요."
        ),
        temperature=0.1,
    )
    return response, True


def _build_context(products: list) -> str:
    lines = []
    for p in products:
        lines.append(f"[제품] {p.model_name}")
        lines.append(f"제조사: {p.manufacturer} | 시리즈: {p.series} | 카테고리: {p.category}")
        if p.specs:
            s = p.specs
            dims = (f"{s.dimension_w}x{s.dimension_h}x{s.dimension_d}mm"
                    if s.dimension_w else None)
            fields = [
                ("외형(WxHxD)", dims),
                ("중량", f"{s.weight_kg}kg" if s.weight_kg else None),
                ("전원", s.input_voltage),
                ("출력방식", s.output_type),
                ("입출력", s.io_points),
                ("통신", s.comm_protocol),
                ("동작온도", s.operating_temp),
                ("보호등급", s.protection_class),
                ("정격출력", s.rated_power),
                ("도면링크", s.drawing_url),
            ]
            lines += [f"{label}: {val}" for label, val in fields if val]
            # 없는 항목을 빼기만 하면 LLM이 빈자리를 일반 지식으로 메운다.
            # 실제로 치수·중량이 NULL인 제품에 '150x180x85mm, 약 6kg'을 지어냈다.
            missing = [label for label, val in fields if not val]
            if missing:
                lines.append(f"미등록 항목(추정 금지): {', '.join(missing)}")
        else:
            lines.append("(스펙 미등록 - 판매자 문의)")
        lines.append("")
    return "\n".join(lines)
