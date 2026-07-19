"""
find_reducer_compat()의 J2S->J4 사이즈 유추 폴백 + 브레이크 접미사 안내 검증.

실제 DB 세션에 임시 Product/Specification을 추가하지만 db.commit()을 호출하지 않고
마지막에 db.rollback()으로 되돌리므로, 실행해도 DB에 영구 흔적이 남지 않는다.
(test_servo_dimension_search.py와 동일한 패턴)
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.db.database import async_session
from app.db.models import Product, Specification, ProductStatus
from app.services.servo_spec_search import find_reducer_compat, _DIMENSION_DISCLAIMER

# 400W로 세팅 — J4_SIZE_TABLE 기준 60mm 프레임 / MR-J4-40A,B와 매칭되어야 함
TEST_DRIVE_MODEL = "MR-TESTJ2S-99A"
TEST_MOTOR_BASE = "HC-TESTMOTOR99"
TEST_MOTOR_BRAKE = "HC-TESTMOTOR99B"

# J4 표에 없는 용량(850W) — 폴백이 발동하면 안 됨
TEST_DRIVE_NOMATCH = "MR-TESTJ2S-88A"
TEST_MOTOR_NOMATCH = "HC-TESTMOTOR88"


async def _seed(db) -> None:
    product = Product(
        model_name=TEST_DRIVE_MODEL, series="TEST-J2S", manufacturer="TestMfr",
        category="servo", status=ProductStatus.ACTIVE,
    )
    db.add(product)
    await db.flush()
    db.add(Specification(
        product_id=product.id,
        extra_specs={"capacity_w": 400, "compatible_motors": [TEST_MOTOR_BASE]},
    ))

    product2 = Product(
        model_name=TEST_DRIVE_NOMATCH, series="TEST-J2S", manufacturer="TestMfr",
        category="servo", status=ProductStatus.ACTIVE,
    )
    db.add(product2)
    await db.flush()
    db.add(Specification(
        product_id=product2.id,
        extra_specs={"capacity_w": 850, "compatible_motors": [TEST_MOTOR_NOMATCH]},
    ))
    await db.flush()


async def main():
    async with async_session() as db:
        await _seed(db)
        failures = []
        try:
            print("=" * 60)
            print(f"1) 브레이크 없는 J2S 모터 폴백: {TEST_MOTOR_BASE}")
            result = await find_reducer_compat(TEST_MOTOR_BASE, db)
            print(result)
            ok = (
                result is not None
                and "J2S 시리즈이지만" in result
                and "HG-KR43" in result
                and "60mm" in result
                and "MR-J4-40A" in result and "MR-J4-40B" in result
                and "브레이크 내장 모델로 사이즈는 동일합니다" not in result
                and _DIMENSION_DISCLAIMER in result
            )
            print("PASS" if ok else "FAIL")
            if not ok:
                failures.append("case1")

            print("=" * 60)
            print(f"2) 브레이크 있는 J2S 모터 폴백: {TEST_MOTOR_BRAKE}")
            result = await find_reducer_compat(TEST_MOTOR_BRAKE, db)
            print(result)
            ok = (
                result is not None
                and "J2S 시리즈이지만" in result
                and "MR-J4-40A" in result
                and "브레이크 내장 모델로 사이즈는 동일합니다" in result
            )
            print("PASS" if ok else "FAIL")
            if not ok:
                failures.append("case2")

            print("=" * 60)
            print(f"3) J4 표에 없는 용량(850W)은 폴백 없이 None: {TEST_MOTOR_NOMATCH}")
            result = await find_reducer_compat(TEST_MOTOR_NOMATCH, db)
            print(result)
            ok = result is None
            print("PASS" if ok else "FAIL")
            if not ok:
                failures.append("case3")

            print("=" * 60)
            print("4) J4 계열 자체 모델(HG-KR43)은 이 폴백 대상이 아님(motor_specs 실측 우선)")
            result = await find_reducer_compat("HG-KR43", db)
            print(result)
            ok = result is None or "J2S 시리즈이지만" not in result
            print("PASS" if ok else "FAIL")
            if not ok:
                failures.append("case4")
        finally:
            await db.rollback()

        if failures:
            print(f"\n{len(failures)}개 실패: {failures}")
            sys.exit(1)
        print("\n모든 테스트 통과.")


asyncio.run(main())
