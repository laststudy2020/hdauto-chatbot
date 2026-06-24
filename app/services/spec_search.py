from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Product, Specification, ProductStatus


async def find_by_spec(
    voltage_v: int | None,
    capacity_kw: float | None,
    db: AsyncSession,
) -> str:
    """전압(V)과 용량(kW)으로 일치하는 인버터 모델을 찾는다."""
    if voltage_v is None or capacity_kw is None:
        return (
            "전압과 용량을 같이 알려주시면 정확한 모델을 찾아드릴게요.\n"
            "예) 220V 2.2kW 인버터 추천해줘"
        )

    voltage_str = f"{voltage_v}V"
    capacity_str = f"{capacity_kw}kW"

    stmt = (
        select(Product)
        .join(Specification, Specification.product_id == Product.id)
        .options(selectinload(Product.specs))
        .where(
            Product.category == "inverter",
            Product.status == ProductStatus.ACTIVE,
            Specification.input_voltage.ilike(f"%{voltage_str}%"),
            Specification.rated_power == capacity_str,
        )
    )
    result = await db.execute(stmt)
    products = result.scalars().all()

    if not products:
        return (
            f"{voltage_v}V {capacity_kw}kW 사양에 맞는 인버터를 찾지 못했습니다.\n"
            f"정확한 사양을 다시 확인해 주시거나 현대자동화로 문의해 주세요."
        )

    if len(products) == 1:
        p = products[0]
        s = p.specs
        lines = [f"{voltage_v}V {capacity_kw}kW 사양에 맞는 모델은 '{p.model_name}'입니다."]
        if s:
            lines.append(f"전원: {s.input_voltage} | 정격출력: {s.rated_power}")
            if s.dimension_w:
                lines.append(
                    f"외형: {s.dimension_w}x{s.dimension_h}x{s.dimension_d}mm | 무게: {s.weight_kg}kg"
                )
        return "\n".join(lines)

    # 같은 전압/용량에 단상·삼상 등 모델이 여러 개 걸리는 경우
    names = ", ".join(p.model_name for p in products)
    return (
        f"{voltage_v}V {capacity_kw}kW 사양에 맞는 모델이 여러 개 있습니다: {names}\n"
        f"단상/삼상 여부를 알려주시면 정확히 추천드릴게요."
    )