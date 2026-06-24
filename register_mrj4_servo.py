"""
MR-J4-A / MR-J4-B 시리즈 서보앰프 등록 마이그레이션 (v2 — v1 대체)

변경 사항 (v1 대비):
- B시리즈 10종 신규 등록 (전기사양 A와 동일 확인됨 — 1.3절 표 대조)
- A시리즈 기존 4종(10A,20A,40A,70A)의 무게(weight_kg)가 잘못 등록되어
  있던 것을 발견 — B시리즈 카탈로그로 확인된 실제값으로 수정
  (예: 10A 1.1kg(추정치, 오류) -> 0.8kg(검증됨))
- 외형치수(W/H/D)는 아직 실제 카탈로그 도면을 확보 못해서 손대지 않음.
  기존 값(40,150,167 / 55,170,167 등)도 같은 시점에 임의로 작성됐을
  가능성이 있어 신뢝도 낮음 — 실제 도면 페이지 확보 전까지는 참고만 할 것.
- 호환 서보모터는 A/B 공통 적용 (전기사양 동일 확인을 근거로 함)

실행: python register_mrj4_servo.py
"""
import asyncio
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Product, Specification, ProductStatus

# (용량W, 출력전류A, 입력전류A, 제어회로소비전력W, 무게kg, 호환서보모터목록)
_BASE_SPECS = [
    (100, 1.1, 0.9, 30, 0.8, ["HG-KR053", "HG-KR13", "HG-MR053", "HG-MR13"]),
    (200, 1.5, 1.5, 30, 0.8, ["HG-KR23", "HG-MR23"]),
    (400, 2.8, 2.6, 30, 1.0, ["HG-KR43", "HG-MR43"]),
    (600, 3.2, 3.2, 30, 1.0, ["HG-SR51", "HG-SR52"]),
    (750, 5.8, 3.8, 30, 1.4, ["HG-KR73", "HG-MR73"]),
    (1000, 6.0, 5.0, 30, 1.4, ["HG-SR81", "HG-SR102"]),
    (2000, 11.0, 10.5, 30, 2.1, ["HG-SR121", "HG-SR201", "HG-SR152", "HG-SR202"]),
    (3500, 17.0, 16.0, 30, 2.3, ["HG-SR301", "HG-SR352"]),
    (5000, 28.0, 21.7, 45, 4.0, ["HG-SR421", "HG-SR502"]),
    (7000, 37.0, 28.9, 45, 6.2, ["HG-SR702"]),
]
_SIZE_SUFFIXES = ["10", "20", "40", "60", "70", "100", "200", "350", "500", "700"]

BRAKE_NOTE = (
    "브레이크 내장형이 필요하면 모델명 끝에 'B'를 추가하세요 "
    "(예: HG-KR13 -> HG-KR13B). 브레이크 내장형은 별도 브레이크 케이블이 필요합니다."
)


async def _upsert(db, model_name, series_suffix, capacity_w, out_a, in_a, ctrl_w, weight_kg, motors, interface_note):
    extra_specs = {
        "capacity_w": capacity_w,
        "rated_output_current_a": out_a,
        "rated_input_current_a": in_a,
        "control_circuit_power_w": ctrl_w,
        "compatible_motors": motors,
        "brake_note": BRAKE_NOTE,
        "interface_note": interface_note,
    }

    result = await db.execute(select(Product).where(Product.model_name == model_name))
    product = result.scalar_one_or_none()

    if product:
        spec_result = await db.execute(select(Specification).where(Specification.product_id == product.id))
        spec = spec_result.scalar_one_or_none()
        if spec:
            old_weight = spec.weight_kg
            spec.weight_kg = weight_kg
            spec.extra_specs = extra_specs

            # 검증 안 된 기존 외형치수는 안전을 위해 비움 (설치공간 오류 방지)
            had_dimensions = spec.dimension_w or spec.dimension_h or spec.dimension_d
            if had_dimensions:
                print(
                    f"  {model_name}: 미검증 치수 비움 "
                    f"(W={spec.dimension_w}, H={spec.dimension_h}, D={spec.dimension_d} -> 확인중)"
                )
                spec.dimension_w = None
                spec.dimension_h = None
                spec.dimension_d = None

            if old_weight and abs(old_weight - weight_kg) > 0.01:
                print(f"  {model_name}: 무게 수정 {old_weight}kg(오류 의심) -> {weight_kg}kg(검증됨)")
            else:
                print(f"  {model_name}: 보완 완료")
        else:
            db.add(Specification(product_id=product.id, weight_kg=weight_kg, extra_specs=extra_specs))
            print(f"  {model_name}: 스펙 신규 추가")
    else:
        new_product = Product(
            model_name=model_name,
            series="MELSERVO-J4",
            manufacturer="Mitsubishi",
            category="servo",
            status=ProductStatus.ACTIVE,
        )
        db.add(new_product)
        await db.flush()
        db.add(Specification(
            product_id=new_product.id,
            weight_kg=weight_kg,
            input_voltage="AC200-240V",
            comm_protocol="USB",
            rated_power=f"{capacity_w}W",
            extra_specs=extra_specs,
        ))
        print(f"  {model_name}: 신규 등록")


async def main():
    async with async_session() as db:
        print("[MR-J4-A 시리즈]")
        for suffix, (capacity_w, out_a, in_a, ctrl_w, weight_kg, motors) in zip(_SIZE_SUFFIXES, _BASE_SPECS):
            await _upsert(
                db, f"MR-J4-{suffix}A", "A", capacity_w, out_a, in_a, ctrl_w, weight_kg, motors,
                interface_note="범용 인터페이스 (아날로그/펄스트레인 등)",
            )

        print("[MR-J4-B 시리즈]")
        for suffix, (capacity_w, out_a, in_a, ctrl_w, weight_kg, motors) in zip(_SIZE_SUFFIXES, _BASE_SPECS):
            await _upsert(
                db, f"MR-J4-{suffix}B", "B", capacity_w, out_a, in_a, ctrl_w, weight_kg, motors,
                interface_note="미쓰비시 고속 시리얼통신 (기계단 엔코더 인터페이스)",
            )

        await db.commit()
        print("\n완료 — A/B 시리즈 총 20개 모델 처리")


if __name__ == "__main__":
    asyncio.run(main())