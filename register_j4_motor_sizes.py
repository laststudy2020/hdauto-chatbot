"""
J4 시리즈(HG-KR/HG-MR) 서보모터 플랜지 프레임 사이즈 등록.

app/services/servo_spec_search.py의 J4_SIZE_TABLE(용량-형명(KR)-형명(MR)-플랜지프레임-
서보드라이브A/B, J2S->J4 사이즈 유추 폴백의 근거 표이기도 함)을 그대로 motor_specs에
반영한다. 실측 상세 전기사양(토크/관성/전장 등) 출처가 없어 frame_size_mm(플랜지 프레임)과
power_w만 등록한다 — register_mrj4_servo.py가 이미 등록해둔 MR-J4-xxA/xxB Product 행에
motor_specs로 병합된다(기존 capacity_w/compatible_motors 등은 보존).

기준표를 여기서 다시 정의하지 않고 import하는 이유: HG-KR/HG-MR 형명 자체를 직접
조회했을 때도 "유추"가 아닌 실측 경로(find_reducer_compat 2단계)로 J2S 유추 답변과
같은 값이 나와야 하는데, 두 곳에 같은 데이터를 따로 두면 조용히 어긋날 수 있기 때문.

알려진 불일치(임의로 고치지 않고 그대로 둠): register_mrj4_servo.py는 MR-J4-60A/60B를
capacity_w=600, compatible_motors=["HG-SR51","HG-SR52"]로 이미 등록했으나, 이 표는
HG-SR52를 500W로 명시한다. 이 스크립트는 드라이브의 top-level capacity_w는 건드리지
않고 motor_specs 하위 항목에만 power_w=500을 기록하므로 기존 값을 덮어쓰지 않는다.

실행: python register_j4_motor_sizes.py [--dry-run]
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Product, Specification
from app.services.servo_spec_search import J4_SIZE_TABLE


def _entry(capacity_w: int, frame_mm: int) -> dict:
    return {
        "power_w": capacity_w,
        "dimensions": {"frame_size_mm": frame_mm},
        "reducers": [],
    }


async def _merge_motor_specs(db, model_name: str, motor_entries: dict) -> str:
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
    motor_specs.update(motor_entries)
    extra_specs["motor_specs"] = motor_specs
    spec.extra_specs = extra_specs

    return f"병합 완료: {model_name} <- {list(motor_entries.keys())}"


async def main(dry_run: bool = False):
    async with async_session() as db:
        for row in J4_SIZE_TABLE:
            motor_entries = {
                row["hg_kr"]: _entry(row["capacity_w"], row["frame_mm"]),
                row["hg_mr"]: _entry(row["capacity_w"], row["frame_mm"]),
            }
            for model_name in (row["drive_a"], row["drive_b"]):
                msg = await _merge_motor_specs(db, model_name, motor_entries)
                print(msg)

        if dry_run:
            await db.rollback()
            print("\n[DRY RUN] 커밋하지 않음 — 위 결과는 실제로 반영되지 않았습니다.")
        else:
            await db.commit()
            print("\n완료 — J4 시리즈(HG-KR/HG-MR) 12종 플랜지 프레임 사이즈 등록")


if __name__ == "__main__":
    asyncio.run(main(dry_run="--dry-run" in sys.argv))
