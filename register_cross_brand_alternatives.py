"""
타사 참고 후보(Cross-brand alternatives) 등록
- 정식 Replacement(동일 제조사 후속 모델, 검증됨)와는 별개로,
  '아직 사양 검증 안 된 참고용 타사 제품' 정보를 단종 제품의
  Specification.extra_specs에 저장한다.
- find_replacement()가 이 정보를 CLOVA 패러프레이즈 없이 고정 문구로 별도 출력.
- 모델 추가 시 아래 딕셔너리에 한 줄씩만 추가하면 됨.

실행: python register_cross_brand_alternatives.py
"""
import asyncio
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Product, Specification

# 단종 모델명 -> 타사 참고 후보 리스트
CROSS_BRAND_ALTERNATIVES = {
    "MR-J2S-40A": [
        {"manufacturer": "야스카와", "model": "SGM7B", "note": "비슷한 사양 확인필요"},
        {"manufacturer": "파나소닉", "model": "MSMA", "note": "비슷한 사양 확인필요"},
        {"manufacturer": "LS산전", "model": "L7SA004", "note": "비슷한 사양 확인필요"},
    ],
}


async def main():
    async with async_session() as db:
        for model_name, alternatives in CROSS_BRAND_ALTERNATIVES.items():
            result = await db.execute(select(Product).where(Product.model_name == model_name))
            product = result.scalar_one_or_none()
            if not product:
                print(f"건너뜀: {model_name} — DB에 해당 제품이 없습니다 (Product 먼저 등록 필요)")
                continue

            spec_result = await db.execute(
                select(Specification).where(Specification.product_id == product.id)
            )
            spec = spec_result.scalar_one_or_none()
            if spec:
                extra = spec.extra_specs or {}
                extra["cross_brand_alternatives"] = alternatives
                spec.extra_specs = extra
                print(f"보완: {model_name} — 타사 후보 {len(alternatives)}개")
            else:
                db.add(Specification(
                    product_id=product.id,
                    extra_specs={"cross_brand_alternatives": alternatives},
                ))
                print(f"신규 스펙 등록: {model_name} — 타사 후보 {len(alternatives)}개")

        await db.commit()
        print("완료")


if __name__ == "__main__":
    asyncio.run(main())