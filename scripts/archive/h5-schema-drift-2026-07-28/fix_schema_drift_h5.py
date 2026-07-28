"""
코드리뷰(2026-07-17) H5 대응: 프로덕션 MariaDB 스키마 드리프트 보정.

최초 마이그레이션(migrate_sqlite_to_mariadb.py)이 pandas.to_sql(if_exists="replace")로
테이블을 생성해 ORM(app/db/models.py)이 선언한 UNIQUE/FK 제약이 실제로는 반영되지
않았다. SHOW CREATE TABLE로 실제 확인한 결과:
  - products.model_name: UNIQUE 없음 (평문 text, 인덱스 없음)
  - inventory/specifications/replacements/stock_alerts/price_history 전부 FK 없음
  - stock_alerts.product_id, price_history.product_id: text로 저장(다른 테이블은 bigint)

실행 전 검증 완료(2026-07-28, 프로덕션 DB 직접 조회):
  - products.model_name 중복 0건, NULL 0건, 최대 길이 17자
  - 모든 *_id 컬럼에 고아 참조(products.id에 없는 값) 0건
  - stock_alerts/price_history.product_id NULL 0건, 값 형태 전부 단순 숫자 문자열
  - inventory/specifications.product_id 중복 0건 (1:1 관계 위반 없음)
  - replacements.old_model_id/new_model_id NULL 0건
  - manufacturer/series/category 최대 길이 각각 15/14/18자 (varchar(50) 여유 충분)
전체 백업: backups/full_backup_pre_h5_<timestamp>.json (7개 테이블 전체 덤프)

이 스크립트는 위 검증이 이미 끝났다는 전제로 곧바로 ALTER TABLE을 실행한다.
데이터가 변경된 이후 재실행 시에는 반드시 사전 검증부터 다시 해야 한다.

실행: python fix_schema_drift_h5.py [--dry-run]
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import text
from app.db.database import engine

# (설명, SQL) — 순서 중요: 타입 변환 → UNIQUE → FK
STATEMENTS = [
    ("products.model_name TEXT -> VARCHAR(100) NOT NULL",
     "ALTER TABLE products MODIFY model_name VARCHAR(100) NOT NULL"),
    ("products.model_name UNIQUE 인덱스 추가",
     "ALTER TABLE products ADD UNIQUE INDEX ix_products_model_name (model_name)"),

    ("stock_alerts.product_id TEXT -> BIGINT NOT NULL",
     "ALTER TABLE stock_alerts MODIFY product_id BIGINT NOT NULL"),
    ("price_history.product_id TEXT -> BIGINT NOT NULL",
     "ALTER TABLE price_history MODIFY product_id BIGINT NOT NULL"),

    ("inventory.product_id UNIQUE 인덱스 추가 (1:1 관계)",
     "ALTER TABLE inventory ADD UNIQUE INDEX ix_inventory_product_id (product_id)"),
    ("specifications.product_id UNIQUE 인덱스 추가 (1:1 관계)",
     "ALTER TABLE specifications ADD UNIQUE INDEX ix_specifications_product_id (product_id)"),

    ("inventory.product_id FK -> products.id",
     "ALTER TABLE inventory ADD CONSTRAINT fk_inventory_product "
     "FOREIGN KEY (product_id) REFERENCES products(id)"),
    ("specifications.product_id FK -> products.id",
     "ALTER TABLE specifications ADD CONSTRAINT fk_specifications_product "
     "FOREIGN KEY (product_id) REFERENCES products(id)"),
    ("replacements.old_model_id FK -> products.id",
     "ALTER TABLE replacements ADD CONSTRAINT fk_replacements_old_model "
     "FOREIGN KEY (old_model_id) REFERENCES products(id)"),
    ("replacements.new_model_id FK -> products.id",
     "ALTER TABLE replacements ADD CONSTRAINT fk_replacements_new_model "
     "FOREIGN KEY (new_model_id) REFERENCES products(id)"),
    ("stock_alerts.product_id FK -> products.id",
     "ALTER TABLE stock_alerts ADD CONSTRAINT fk_stock_alerts_product "
     "FOREIGN KEY (product_id) REFERENCES products(id)"),
    ("price_history.product_id FK -> products.id",
     "ALTER TABLE price_history ADD CONSTRAINT fk_price_history_product "
     "FOREIGN KEY (product_id) REFERENCES products(id)"),
]


async def main(dry_run: bool = False):
    async with engine.connect() as conn:
        for label, sql in STATEMENTS:
            if dry_run:
                print(f"[DRY RUN] {label}\n  {sql}")
                continue
            try:
                await conn.execute(text(sql))
                await conn.commit()
                print(f"[OK] {label}")
            except Exception as e:
                print(f"[FAIL] {label}: {e}")
                print("중단 — 이후 단계는 이 실패에 의존할 수 있으므로 실행하지 않음.")
                return
    if not dry_run:
        print("\n완료 — H5 스키마 드리프트 보정 (UNIQUE/FK) 적용됨.")


if __name__ == "__main__":
    asyncio.run(main(dry_run="--dry-run" in sys.argv))
