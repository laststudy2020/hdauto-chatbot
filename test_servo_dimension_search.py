"""
서보모터 물리치수 + 감속기 결합사양(motor_specs) 검색 기능 검증.
- 드라이브 모델명 -> 결합 감속기 조회 (find_reducer_compat)
- 모터 모델명 -> 결합 감속기 조회 (find_reducer_compat, 모터명 경로)
- 감속기 모델명 -> 호환 모터 역검색 (find_motors_by_reducer)
- 미등록 모델 -> None 정상 반환
- 응답에 면책 문구(disclaimer)가 자동 첨부되는지 확인

실제 DB 세션에 테스트용 임시 Product/Specification을 추가하지만 db.commit()을 호출하지
않고 마지막에 db.rollback()으로 되돌리므로, 실행해도 DB에 영구 흔적이 남지 않는다.
"""
import asyncio

from app.db.database import async_session
from app.db.models import Product, Specification, ProductStatus
from app.services.servo_spec_search import (
    find_reducer_compat, find_motors_by_reducer, _DIMENSION_DISCLAIMER,
)

TEST_DRIVE_MODEL = "TESTDRV-9999"
TEST_MOTOR_MODEL = "TESTMOTOR-A"
TEST_REDUCER_MODEL = "TESTRED-10"


async def _seed_temp_data(db) -> None:
    product = Product(
        model_name=TEST_DRIVE_MODEL,
        series="TEST-SERIES",
        manufacturer="TestMfr",
        category="servo",
        status=ProductStatus.ACTIVE,
    )
    db.add(product)
    await db.flush()

    spec = Specification(
        product_id=product.id,
        extra_specs={
            "capacity_w": 400,
            "compatible_motors": [TEST_MOTOR_MODEL],
            "motor_specs": {
                TEST_MOTOR_MODEL: {
                    "dimensions": {
                        "outer_diameter_mm": 60,
                        "overall_length_mm": 120,
                        "shaft_diameter_mm": 8,
                        "flange_spec": "60x60mm, 4-M5",
                    },
                    "reducers": [
                        {
                            "model": TEST_REDUCER_MODEL,
                            "reduction_ratio": "1/10",
                            "coupling_note": "전용 어댑터 별매",
                        }
                    ],
                }
            },
        },
    )
    db.add(spec)
    await db.flush()


async def main():
    async with async_session() as db:
        await _seed_temp_data(db)

        try:
            print("=" * 60)
            print(f"1) 드라이브 모델명으로 조회: {TEST_DRIVE_MODEL}")
            print("-" * 60)
            result = await find_reducer_compat(TEST_DRIVE_MODEL, db)
            print(result)
            assert result is not None, "드라이브 모델명 매칭 실패"
            assert TEST_MOTOR_MODEL in result, "모터명이 결과에 없음"
            assert TEST_REDUCER_MODEL in result, "감속기 모델이 결과에 없음"
            assert _DIMENSION_DISCLAIMER in result, "면책 문구 누락"
            print("PASS\n")

            print("=" * 60)
            print(f"2) 모터 모델명으로 조회: {TEST_MOTOR_MODEL}")
            print("-" * 60)
            result = await find_reducer_compat(TEST_MOTOR_MODEL, db)
            print(result)
            assert result is not None, "모터 모델명 매칭 실패"
            assert TEST_REDUCER_MODEL in result, "감속기 모델이 결과에 없음"
            assert _DIMENSION_DISCLAIMER in result, "면책 문구 누락"
            print("PASS\n")

            print("=" * 60)
            print(f"3) 감속기 모델명으로 호환 모터 역검색: {TEST_REDUCER_MODEL}")
            print("-" * 60)
            result = await find_motors_by_reducer(TEST_REDUCER_MODEL, db)
            print(result)
            assert result is not None, "감속기 역검색 실패"
            assert TEST_MOTOR_MODEL in result, "모터명이 결과에 없음"
            assert TEST_DRIVE_MODEL in result, "드라이브명이 결과에 없음"
            assert _DIMENSION_DISCLAIMER in result, "면책 문구 누락"
            print("PASS\n")

            print("=" * 60)
            print("4) 미등록 모델 -> None 반환 확인")
            print("-" * 60)
            no_match_drive = await find_reducer_compat("존재하지않는모델XYZ999", db)
            no_match_reducer = await find_motors_by_reducer("존재하지않는감속기XYZ999", db)
            print(f"find_reducer_compat: {no_match_drive}")
            print(f"find_motors_by_reducer: {no_match_reducer}")
            assert no_match_drive is None, "미등록 모델인데 None이 아님"
            assert no_match_reducer is None, "미등록 감속기인데 None이 아님"
            print("PASS\n")

            print("모든 테스트 통과.")
        finally:
            # 커밋한 적이 없으므로 rollback으로 테스트 데이터를 완전히 폐기
            await db.rollback()


asyncio.run(main())
