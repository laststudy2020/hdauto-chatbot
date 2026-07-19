"""
J4 시리즈(HG-KR/HG-MR) 서보모터 플랜지 프레임 사이즈 등록.

사용자 제공 기준표(용량-형명(KR)-형명(MR)-플랜지프레임-서보드라이브A/B)를 그대로
motor_specs에 반영한다. 실측 상세 전기사양(토크/관성/전장 등) 출처가 없어
frame_size_mm(플랜지 프레임)과 power_w만 등록한다 — register_mrj4_servo.py가 이미
등록해둔 MR-J4-xxA/xxB Product 행에 motor_specs로 병합된다(기존 capacity_w/
compatible_motors 등은 보존).

이 표는 app/services/servo_spec_search.py의 J4_SIZE_TABLE(J2S->J4 사이즈 유추 폴백의
근거 표)과 동일한 원본 데이터다 — HG-KR/HG-MR 형명 자체를 직접 조회했을 때도 "유추"가
아닌 실측 경로(find_reducer_compat 2단계)로 같은 값이 나오게 하기 위함.

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

# (용량W, 형명KR, 형명MR, 플랜지프레임mm, 드라이브A, 드라이브B)
J4_SIZE_ROWS = [
    (50, "HG-KR053", "HG-MR053", 40, "MR-J4-10A", "MR-J4-10B"),
    (100, "HG-KR13", "HG-MR13", 40, "MR-J4-10A", "MR-J4-10B"),
    (200, "HG-KR23", "HG-MR23", 60, "MR-J4-20A", "MR-J4-20B"),
    (400, "HG-KR43", "HG-MR43", 60, "MR-J4-40A", "MR-J4-40B"),
    (750, "HG-KR73", "HG-MR73", 80, "MR-J4-70A", "MR-J4-70B"),
    (500, "HG-SR52", "HG-MR52", 130, "MR-J4-60A", "MR-J4-60B"),
    (1000, "HG-SR102", "HG-MR102", 130, "MR-J4-100A", "MR-J4-100B"),
    (1500, "HG-SR152", "HG-MR152", 130, "MR-J4-200A", "MR-J4-200B"),
    (2000, "HG-SR202", "HG-MR202", 176, "MR-J4-200A", "MR-J4-200B"),
    (3500, "HG-SR352", "HG-MR352", 176, "MR-J4-350A", "MR-J4-350B"),
    (5000, "HG-SR502", "HG-MR502", 176, "MR-J4-500A", "MR-J4-500B"),
    (7000, "HG-SR702", "HG-MR702", 176, "MR-J4-700A", "MR-J4-700B"),
]


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
        for capacity_w, hg_kr, hg_mr, frame_mm, drive_a, drive_b in J4_SIZE_ROWS:
            motor_entries = {
                hg_kr: _entry(capacity_w, frame_mm),
                hg_mr: _entry(capacity_w, frame_mm),
            }
            for model_name in (drive_a, drive_b):
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
