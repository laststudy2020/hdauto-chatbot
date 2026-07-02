"""
get_inventory_status() 전체 흐름 테스트.
- DB에 등록된 모델 (기존 경로)
- DB에 없는 모델이지만 스마트스토어에 있는 것 (새로 추가한 _check_naver_directly 경로)
- 존재하지 않는 모델 (재고 없음 정상 응답 확인)
"""
import asyncio

from app.db.database import async_session
from app.services.inventory import get_inventory_status


TEST_MODELS = [
    "MR-J2S-40B",       # 오늘 계속 다뤘던 모델 - DB 등록 여부와 무관하게 재고 나와야 함
    "SV022iG5A-4",      # 이전에 정상 조회됐던 인버터 (회귀 테스트용)
    "존재하지않는모델XYZ999",  # 정상적으로 "재고 없음" 응답하는지 확인
]


async def main():
    async with async_session() as db:
        for model in TEST_MODELS:
            print("=" * 60)
            print(f"조회 모델: {model}")
            print("-" * 60)
            try:
                result = await get_inventory_status(model, db)
                print(result)
            except Exception as e:
                print(f"❌ 예외 발생: {type(e).__name__}: {e}")
            print()


asyncio.run(main())