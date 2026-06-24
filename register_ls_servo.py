"""
LS산전 서보드라이브 2종 등록
- XDL-L7NH: 네트워크형(All-in-One Type)
- L7S: 표준 I/O Type
주의: 이 자료는 '모델명 해독 규칙' 도표이며 실제 판매 SKU 리스트가 아님.
완전한 모델명을 만들려면 입력전압/인코더/옵션까지 정해야 하므로,
아래는 220V·표준옵션을 대표값으로 선택해 구성한 것 (400V 등 다른 옵션은 미등록).
호환 서보모터는 미쓰비시/야스카와처럼 정식 조합표가 없어 구체적 모델명 대신
안내문구(motor_compat_note)로 처리함 — 가짜 모델명 조합을 방지하기 위함.

실행: python register_ls_servo.py
"""
import asyncio
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Product, Specification, ProductStatus

MOTOR_COMPAT_NOTE = (
    "동일 용량대의 LS APM(실축형)/XML(중공축형 등) 시리즈 서보모터와 호환 가능합니다. "
    "정확한 모터 형식(인코더 타입, 정격회전수, 옵션)은 주문 시 확인이 필요합니다."
)
BRAKE_NOTE = "브레이크가 필요하면 모터 옵션사양에서 'Brake 부착'(옵션코드 2 또는 3)을 선택하세요."

# (용량코드, 용량W)
XDL_L7NH_CAPACITIES = [
    ("001", 100), ("002", 200), ("004", 400), ("008", 750), ("010", 1000),
    ("020", 2000), ("035", 3500), ("050", 5000), ("075", 7500),
    ("110", 11000), ("150", 15000),
]
L7S_CAPACITIES = [
    ("001", 100), ("002", 200), ("004", 400), ("008", 750), ("010", 1000),
    ("020", 2000), ("035", 3500), ("050", 5000), ("075", 7500), ("150", 15000),
]


async def _upsert(db, model_name, series, capacity_w, interface_note):
    extra_specs = {
        "capacity_w": capacity_w,
        "compatible_motors": [],
        "motor_compat_note": MOTOR_COMPAT_NOTE,
        "interface_note": interface_note,
        "brake_note": BRAKE_NOTE,
    }
    result = await db.execute(select(Product).where(Product.model_name == model_name))
    product = result.scalar_one_or_none()

    if product:
        spec_result = await db.execute(select(Specification).where(Specification.product_id == product.id))
        spec = spec_result.scalar_one_or_none()
        if spec:
            spec.extra_specs = extra_specs
        else:
            db.add(Specification(product_id=product.id, extra_specs=extra_specs))
        print(f"보완: {model_name}")
    else:
        new_product = Product(
            model_name=model_name, series=series, manufacturer="LS",
            category="servo", status=ProductStatus.ACTIVE,
        )
        db.add(new_product)
        await db.flush()
        db.add(Specification(
            product_id=new_product.id,
            input_voltage="AC220V (대표값, 400V 옵션 별도)",
            rated_power=f"{capacity_w}W",
            extra_specs=extra_specs,
        ))
        print(f"신규 등록: {model_name}")


async def main():
    async with async_session() as db:
        print("[XDL-L7NH 시리즈 (네트워크형)]")
        for code, capacity_w in XDL_L7NH_CAPACITIES:
            model_name = f"XDL-L7NHA{code}U"  # 200Vac, Universal 인코더, 표준옵션 대표값
            await _upsert(db, model_name, "XDL-L7NH", capacity_w, "네트워크형 (All-in-One Type)")

        print("[L7S 시리즈 (표준 I/O Type)]")
        for code, capacity_w in L7S_CAPACITIES:
            model_name = f"L7SA{code}A"  # 220Vac, Quadrature 인코더, 표준옵션 대표값
            await _upsert(db, model_name, "L7S", capacity_w, "표준 I/O Type")

        await db.commit()
        print(f"\n완료 — XDL-L7NH {len(XDL_L7NH_CAPACITIES)}개, L7S {len(L7S_CAPACITIES)}개 처리")


if __name__ == "__main__":
    asyncio.run(main())