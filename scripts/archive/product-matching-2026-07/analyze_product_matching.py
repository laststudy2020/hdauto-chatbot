"""
products_raw.json을 분석해서:
  1. 모델명(상품명 첫 토큰)이 동일한 채널상품이 몇 건이나 중복되는지 집계
  2. DB의 Product.model_name과 비교해 매칭 가능 여부를 미리 점검

이 스크립트는 DB를 건드리지 않는다. 순수 분석/리포트 용도.

실행:
  python analyze_product_matching.py
"""
import asyncio
import json
import re
from collections import defaultdict

from sqlalchemy import select

from app.db.database import async_session
from app.db.models import Product

INPUT_FILE = "products_raw.json"


def extract_model_prefix(name: str) -> str:
    """상품명 맨 앞 모델명 토큰 추출.
    구분자: 공백, '(', '[' 중 가장 먼저 나오는 위치까지를 모델명으로 간주.
    """
    if not name:
        return ""
    # 가장 먼저 나오는 구분자 위치 찾기
    positions = [p for p in (name.find(" "), name.find("("), name.find("[")) if p != -1]
    cut = min(positions) if positions else len(name)
    return name[:cut].strip().upper()


async def main():
    with open(INPUT_FILE, encoding="utf-8") as f:
        items = json.load(f)

    print(f"전체 상품 {len(items)}건 분석 시작\n")

    # 1. 모델명(추정) 기준 그룹핑
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        model = extract_model_prefix(item.get("name", ""))
        groups[model].append(item)

    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    unique_groups = {k: v for k, v in groups.items() if len(v) == 1}

    print(f"추출된 고유 모델명(추정) 종류: {len(groups)}개")
    print(f"  - 1건만 있는 모델명: {len(unique_groups)}개  (자동 매칭 후보)")
    print(f"  - 2건 이상 중복된 모델명: {len(dup_groups)}개  (수동 판단 필요)\n")

    print("=" * 60)
    print("중복 모델명 TOP 20 (건수 많은 순)")
    print("=" * 60)
    sorted_dups = sorted(dup_groups.items(), key=lambda x: -len(x[1]))
    for model, products in sorted_dups[:20]:
        print(f"\n  [{model}] {len(products)}건")
        for p in products:
            print(f"    - 채널상품번호={p['channel_product_no']} | {p['name']}")

    # 2. DB의 Product.model_name과 매칭 가능성 점검
    async with async_session() as db:
        db_products = (await db.execute(select(Product.model_name))).scalars().all()

    db_models = {m.strip().upper() for m in db_products if m}
    print(f"\n\nDB에 등록된 제품 수: {len(db_models)}건")

    matched_unique = [m for m in db_models if m in unique_groups]
    matched_dup = [m for m in db_models if m in dup_groups]
    unmatched = [m for m in db_models if m not in groups]

    print(f"  - 스마트스토어에서 단일 매칭 가능: {len(matched_unique)}건 (바로 자동 반영 가능)")
    print(f"  - 스마트스토어에서 중복 존재(수동 선택 필요): {len(matched_dup)}건")
    print(f"  - 스마트스토어에서 매칭 후보 없음: {len(unmatched)}건 (신규/표기차이 의심)")

    if unmatched:
        print(f"\n매칭 안 된 DB 모델명 샘플 (최대 20개):")
        for m in unmatched[:20]:
            print(f"    - {m}")

    # 결과 저장 (2단계에서 활용)
    output = {
        "matched_unique": matched_unique,
        "matched_dup": matched_dup,
        "unmatched": unmatched,
        "dup_group_detail": {
            k: [{"channel_product_no": p["channel_product_no"],
                 "origin_product_no": p["origin_product_no"],
                 "name": p["name"],
                 "stock_quantity": p["stock_quantity"]} for p in v]
            for k, v in dup_groups.items() if k in matched_dup
        },
    }
    with open("matching_analysis.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n분석 결과 저장 완료 -> matching_analysis.json")


if __name__ == "__main__":
    asyncio.run(main())