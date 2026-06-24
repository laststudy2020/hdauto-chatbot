"""
야스카와 Σ-7 시리즈(SGD7S) 서보팩 15종 등록
- 미쓰비시 MR-J4와 동일한 extra_specs 구조(capacity_w 등) 사용
  -> find_servo_by_capacity()가 제조사 구분 없이 같은 용량을 자동으로 같이 보여줌
- 호환 서보모터(SGM7J/SGM7A/SGM7P/SGM7G)는 마찬가지로 별도 Product가 아닌
  extra_specs 리스트로 저장 (모터 단독 판매/검색이 필요해지면 승격)
- 무게 데이터는 이번 카탈로그에 없어 비워둠
- 전기사양은 3상AC200V 기준 (R70A~5R5A는 단상/DC270V도 지원하나 이번엔 3상값만 등록)

실행: python register_yaskawa_servo.py
"""
import asyncio
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Product, Specification, ProductStatus

# (팩코드, 용량W, 연속출력전류A, 순간최대출력전류A, 입력전류A, 합계전력손실W, 호환서보모터목록)
YASKAWA_SGD7S = [
    ("R70A", 50, 0.66, 2.1, 0.4, 22.1, ["SGM7J-A5A", "SGM7A-A5A"]),
    ("R90A", 100, 0.91, 3.2, 0.8, 24.3, ["SGM7J-01A", "SGM7A-01A", "SGM7P-01A"]),
    ("1R6A", 200, 1.6, 5.9, 1.3, 30.5, ["SGM7J-C2A", "SGM7J-02A", "SGM7A-C2A", "SGM7A-02A"]),
    ("2R8A", 400, 2.8, 9.3, 2.5, 41.0, ["SGM7J-04A", "SGM7A-04A", "SGM7P-02A", "SGM7P-04A"]),
    ("3R8A", 500, 3.8, 11.0, 3.0, 45.1, ["SGM7G-03A", "SGM7G-05A"]),
    ("5R5A", 750, 5.5, 16.9, 4.1, 68.8, ["SGM7J-06A", "SGM7J-08A", "SGM7A-06A", "SGM7A-08A", "SGM7P-08A"]),
    ("7R6A", 1000, 7.6, 17.0, 5.7, 78.6, ["SGM7G-09A"]),
    ("120A", 1500, 11.6, 28.0, 7.3, 97.8, ["SGM7A-10A", "SGM7A-15A", "SGM7P-15A", "SGM7G-13A"]),
    ("180A", 2000, 18.5, 42.0, 10.0, 149.9, ["SGM7A-20A", "SGM7G-20A"]),
    ("200A", 3000, 19.6, 56.0, 15.0, 151.8, ["SGM7A-25A", "SGM7A-30A", "SGM7G-30A(2.4kW사양)"]),
    ("330A", 5000, 32.9, 84.0, 25.0, 326.7, ["SGM7A-40A", "SGM7A-50A", "SGM7G-30A(2.9kW사양)", "SGM7G-44A"]),
    ("470A", 6000, 46.9, 110.0, 29.0, 312.4, ["SGM7G-55A"]),
    ("550A", 7500, 54.7, 130.0, 37.0, 390.8, ["SGM7A-70A", "SGM7G-75A"]),
    ("590A", 11000, 58.6, 140.0, 54.0, 479.7, ["SGM7G-1AA"]),
    ("780A", 15000, 78.0, 170.0, 73.0, 647.0, ["SGM7G-1EA"]),
]

INTERFACE_NOTE = "아날로그 전압·펄스열 지령형 (인터페이스 사양 00)"
BRAKE_NOTE = (
    "브레이크 내장형이 필요하면 모터 형식 7번째 자릿수 옵션에서 "
    "'24V 브레이크 장착'을 선택한 모델을 사용하세요."
)


async def main():
    async with async_session() as db:
        for pack_code, capacity_w, cont_a, max_a, in_a, loss_w, motors in YASKAWA_SGD7S:
            model_name = f"SGD7S-{pack_code}"
            extra_specs = {
                "capacity_w": capacity_w,
                "rated_output_current_a": cont_a,
                "max_instant_current_a": max_a,
                "rated_input_current_a": in_a,
                "total_power_loss_w": loss_w,
                "compatible_motors": motors,
                "interface_note": INTERFACE_NOTE,
                "brake_note": BRAKE_NOTE,
            }

            result = await db.execute(select(Product).where(Product.model_name == model_name))
            product = result.scalar_one_or_none()

            if product:
                spec_result = await db.execute(
                    select(Specification).where(Specification.product_id == product.id)
                )
                spec = spec_result.scalar_one_or_none()
                if spec:
                    spec.extra_specs = extra_specs
                else:
                    db.add(Specification(product_id=product.id, extra_specs=extra_specs))
                print(f"보완: {model_name}")
            else:
                new_product = Product(
                    model_name=model_name,
                    series="Sigma-7",
                    manufacturer="Yaskawa",
                    category="servo",
                    status=ProductStatus.ACTIVE,
                )
                db.add(new_product)
                await db.flush()
                db.add(Specification(
                    product_id=new_product.id,
                    input_voltage="AC200-240V",
                    rated_power=f"{capacity_w}W",
                    extra_specs=extra_specs,
                ))
                print(f"신규 등록: {model_name}")

        await db.commit()
        print(f"\n완료 — 야스카와 SGD7S {len(YASKAWA_SGD7S)}개 모델 처리")


if __name__ == "__main__":
    asyncio.run(main())