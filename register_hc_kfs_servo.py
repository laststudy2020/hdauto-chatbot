"""
미쓰비시 HC-KFS 시리즈 서보모터(053/13/23/43(B)) 물리치수+전기사양 등록.

데이터 출처: docs/datasheets/mitsubishi_j2s/미쓰비시J2S시리즈서보모터.pdf
- 37페이지(p.36) "서보모터 HC-KFS 시리즈 사양" -> 전기사양(출력/토크/속도/관성/질량)
- 82페이지(p.81) "외형치수도 - 서보모터외형도" -> 치수(프레임/몸체/전장/축경/축길이/플랜지)

이번 배치에서 제외한 것 (불확실하거나 대응 드라이브 행이 없음):
- HC-KFS46 / HC-KFS410 (고속회전형): 전용 앰프가 MR-J2S-70A/B-U005/-U006 옵션 SKU라
  이 드라이브 Product 행이 DB에 없음. 드라이브 행부터 별도로 등록해야 함.
- HC-SFS 전 모델: 볼트원(PCD)이 ø145/ø165(또는 ø200/ø230) 중 어느 쪽인지 도면 확인이
  아직 끝나지 않아 이번 배치에서 제외.

기존 register_mrj2s_manual.py가 이미 만들어둔 MR-J2S-{10,20,40}{A,B,A1,B1} 드라이브
Product 행에 motor_specs만 병합한다 (capacity_w/compatible_motors/interface_note/
brake_note 등 기존 extra_specs 값은 보존).

reducers는 빈 리스트로 남겨둠 — 감속기 카탈로그 데이터가 별도로 들어오면 채울 것
(app/services/servo_spec_search.py의 find_reducer_compat/find_motors_by_reducer가
이미 "reducers": [] 를 "감속기 호환 정보 미등록"으로 처리하도록 구현되어 있음).

실행: python register_hc_kfs_servo.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Product, Specification

# 코드별로 묶인 치수 (같은 프레임끼리는 축경/축길이/플랜지가 동일 — 82페이지 확인)
FRAME_40 = {
    "frame_size_mm": 40, "body_size_mm": 42,
    "shaft_diameter_mm": 8, "shaft_length_mm": 25,
    "flange_spec": "2-ø4.5 (볼트원 ø46)",
}
FRAME_60 = {
    "frame_size_mm": 60, "body_size_mm": 62,
    "shaft_diameter_mm": 14, "shaft_length_mm": 30,
    "flange_spec": "4-ø5.8 (볼트원 ø70)",
}

# 모터형명 -> (앰프코드, 전기사양, 치수(L/L브레이크 제외 공통치수는 FRAME_* 병합))
MOTORS = {
    "HC-KFS053": {
        "code": "10",
        "power_w": 50, "rated_torque_nm": 0.16, "max_torque_nm": 0.48,
        "rated_speed_rpm": 3000, "max_speed_rpm": 4500,
        "inertia_j": 0.053, "inertia_j_brake": 0.056,
        "mass_kg": 0.4, "mass_kg_brake": 0.75,
        "dimensions": {**FRAME_40, "overall_length_mm": 81.5, "overall_length_mm_brake": 109.5},
    },
    "HC-KFS13": {
        "code": "10",
        "power_w": 100, "rated_torque_nm": 0.32, "max_torque_nm": 0.95,
        "rated_speed_rpm": 3000, "max_speed_rpm": 4500,
        "inertia_j": 0.084, "inertia_j_brake": 0.087,
        "mass_kg": 0.53, "mass_kg_brake": 0.89,
        "dimensions": {**FRAME_40, "overall_length_mm": 96.5, "overall_length_mm_brake": 124.5},
    },
    "HC-KFS23": {
        "code": "20",
        "power_w": 200, "rated_torque_nm": 0.64, "max_torque_nm": 1.9,
        "rated_speed_rpm": 3000, "max_speed_rpm": 4500,
        "inertia_j": 0.260, "inertia_j_brake": 0.310,
        "mass_kg": 0.99, "mass_kg_brake": 1.6,
        "dimensions": {**FRAME_60, "overall_length_mm": 99.5, "overall_length_mm_brake": 131.5},
    },
    "HC-KFS43": {
        "code": "40",
        "power_w": 400, "rated_torque_nm": 1.3, "max_torque_nm": 3.8,
        "rated_speed_rpm": 3000, "max_speed_rpm": 4500,
        "inertia_j": 0.460, "inertia_j_brake": 0.510,
        "mass_kg": 1.45, "mass_kg_brake": 2.1,
        "dimensions": {**FRAME_60, "overall_length_mm": 124.5, "overall_length_mm_brake": 156.5},
    },
}

# 코드 -> 병합 대상 드라이브 모델명 (register_mrj2s_manual.py가 이미 등록해둔 행)
DRIVES_BY_CODE = {
    "10": ["MR-J2S-10A", "MR-J2S-10B", "MR-J2S-10A1", "MR-J2S-10B1"],
    "20": ["MR-J2S-20A", "MR-J2S-20B", "MR-J2S-20A1", "MR-J2S-20B1"],
    "40": ["MR-J2S-40A", "MR-J2S-40B", "MR-J2S-40A1", "MR-J2S-40B1"],
}

# 코드별로 병합할 모터 항목들 (하나의 드라이브가 여러 모터를 지원 — 예: code 10 -> 053, 13)
ENTRIES_BY_CODE: dict[str, dict] = {}
for motor_name, data in MOTORS.items():
    code = data["code"]
    entry = {k: v for k, v in data.items() if k != "code"}
    entry["reducers"] = []
    ENTRIES_BY_CODE.setdefault(code, {})[motor_name] = entry


async def _merge_motor_specs(db, model_name: str, code: str) -> str:
    result = await db.execute(select(Product).where(Product.model_name == model_name))
    product = result.scalar_one_or_none()
    if not product:
        return f"스킵 (드라이브 행 없음): {model_name}"

    spec_result = await db.execute(select(Specification).where(Specification.product_id == product.id))
    spec = spec_result.scalar_one_or_none()
    if not spec:
        return f"스킵 (Specification 없음): {model_name}"

    extra_specs = dict(spec.extra_specs or {})
    motor_specs = dict(extra_specs.get("motor_specs") or {})
    motor_specs.update(ENTRIES_BY_CODE[code])
    extra_specs["motor_specs"] = motor_specs
    spec.extra_specs = extra_specs

    return f"병합 완료: {model_name} <- {list(ENTRIES_BY_CODE[code].keys())}"


async def main():
    async with async_session() as db:
        for code, drive_names in DRIVES_BY_CODE.items():
            for model_name in drive_names:
                msg = await _merge_motor_specs(db, model_name, code)
                print(msg)

        await db.commit()
        print("\n완료 — HC-KFS053/13/23/43(B) 치수+전기사양 등록")


if __name__ == "__main__":
    asyncio.run(main())
