"""
manual_mappings.json에 등록된 수동 검증 매핑을 DB에 강제 반영한다.
finalize_matching.py의 KNOWN_BAD_MATCHES 제외 로직을 우회해서
사람이 직접 확인한 origin_product_no를 심는다.

사전 필수:
  - manual_mappings.json에 origin_product_no, channel_product_no를
    실제 값으로 채워넣을 것 (placeholder 상태로는 실행 거부됨)

실행:
  python apply_manual_mappings.py --dry-run   # 반영될 내용만 미리 확인
  python apply_manual_mappings.py             # 실제 DB 반영
"""
import asyncio
import json
import sys

from sqlalchemy import select

from app.db.database import async_session
from app.db.models import Product

MAPPINGS_FILE = "manual_mappings.json"
PLACEHOLDER_MARKERS = ("여기에_", "TODO", "TBD")


def is_placeholder(value: str) -> bool:
    return any(marker in str(value) for marker in PLACEHOLDER_MARKERS)


async def main():
    dry_run = "--dry-run" in sys.argv

    with open(MAPPINGS_FILE, encoding="utf-8") as f:
        mappings = json.load(f)

    ready = {}
    skipped = {}

    for model, info in mappings.items():
        if is_placeholder(info.get("origin_product_no", "")) or is_placeholder(
            info.get("channel_product_no", "")
        ):
            skipped[model] = "origin_product_no 또는 channel_product_no가 placeholder 상태"
            continue
        ready[model] = info

    print(f"반영 대상: {len(ready)}건 -> {list(ready.keys())}")
    if skipped:
        print(f"건너뜀(값 미입력): {len(skipped)}건 -> {list(skipped.keys())}")

    if not ready:
        print("\n반영할 항목이 없습니다. manual_mappings.json에 실제 값을 채워넣으세요.")
        return

    if dry_run:
        print("\ndry-run 모드: DB에 반영하지 않았습니다.")
        for model, info in ready.items():
            print(f"  {model} -> origin_product_no={info['origin_product_no']}")
        return

    updated_count = 0
    not_found = []

    async with async_session() as db:
        for model, info in ready.items():
            stmt = select(Product).where(Product.model_name == model)
            product = (await db.execute(stmt)).scalar_one_or_none()
            if product:
                product.smartstore_product_id = str(info["channel_product_no"])
                product.origin_product_no = str(info["origin_product_no"])
                product.inventory_sync_enabled = True
                updated_count += 1
            else:
                not_found.append(model)

        await db.commit()

    print(f"\nDB 반영 완료: {updated_count}건")
    if not_found:
        print(f"DB에 해당 model_name 없음: {not_found}")


if __name__ == "__main__":
    asyncio.run(main())