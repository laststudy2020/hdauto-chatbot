"""
mysqldump가 로컬에 없어서 대체하는 specifications 테이블 백업 스크립트.

register_hc_kfs_servo.py 실행 전, 프로덕션(NAS MariaDB) specifications 테이블 전체를
JSON으로 스냅샷한다. 각 행에 product_id로 조인한 model_name을 같이 넣어서 사람이
읽어도 어떤 제품 스펙인지 바로 알 수 있게 한다.

복구가 필요하면 backups/specifications_restore.py 참고 (같이 생성됨) —
JSON을 읽어 model_name으로 Product를 찾고 Specification의 모든 컬럼을 덮어쓴다.

실행: python backup_specifications.py
"""
import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.database import async_session
from app.db.models import Product, Specification

SPEC_COLUMNS = [
    "id", "product_id", "dimension_w", "dimension_h", "dimension_d", "weight_kg",
    "input_voltage", "output_type", "io_points", "comm_protocol", "operating_temp",
    "protection_class", "mounting_type", "rated_power", "extra_specs",
    "drawing_url", "catalog_page",
]


async def main():
    async with async_session() as db:
        result = await db.execute(
            select(Specification, Product.model_name)
            .join(Product, Product.id == Specification.product_id)
            .order_by(Specification.id)
        )
        rows = result.all()

        backup = []
        for spec, model_name in rows:
            row = {col: getattr(spec, col) for col in SPEC_COLUMNS}
            row["model_name"] = model_name
            backup.append(row)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = f"backups/specifications_backup_{timestamp}.json"

        import os
        os.makedirs("backups", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False, indent=2, default=str)

        print(f"백업 완료: {out_path} ({len(backup)}행)")


if __name__ == "__main__":
    asyncio.run(main())
