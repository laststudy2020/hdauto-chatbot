"""프로덕션 MariaDB 타입 드리프트 보정 (DATETIME / 숫자 / BOOLEAN).

H5(2026-07-28, UNIQUE·FK 보정)에서 "위험 대비 효과가 낮다"며 보류했던 타입 드리프트 중,
실제로 기능 결함이 확인된 부분만 보정한다. 최초 마이그레이션이
migrate_sqlite_to_mariadb.py의 pandas.to_sql(if_exists="replace")로 테이블을 만들어
ORM(app/db/models.py)이 선언한 타입/기본값이 전혀 반영되지 않은 것이 원인.

확인된 결함 (2026-07-29, 재고알림 기능 실동작 테스트 중 발견):
  1) stock_alerts.sent_at / price_history.checked_at 이 text 컬럼이라
     ORM의 server_default=func.now()가 적용되지 않아(기본값은 CREATE TABLE 시점에만
     반영됨) 모든 알림 기록의 시각이 NULL. 알림은 나가지만 언제 나갔는지 남지 않음.
     같은 이유로 products.created_at 133건 중 97건, updated_at 2건, replacements.created_at
     1건도 NULL.
  2) products.our_price가 double이라 관리자 카카오 알림에 "판매단가: 420,000.0원"으로
     소수점이 붙어 출력됨(ORM 선언은 Integer).

실행 전 검증 완료 (프로덕션 DB 직접 조회, scratchpad/survey_type_drift.py):
  - DateTime 후보 7개 컬럼: CAST(... AS DATETIME) 실패 0건 (NULL은 NULL로 유지)
  - products.our_price: 비숫자 0건, 소수부 있는 값 0건 (133건 중 109건 NULL)
  - price_history의 숫자 text 컬럼 6종: 비숫자 0건, 소수부 있는 값 0건
  - stock_alerts.resolved = '0' 3건 / price_history.needs_adjustment = '1' 2건뿐
전체 백업: backups/full_backup_pre_typefix_20260729T014452Z.json (9개 테이블 전체 덤프)

기존 NULL 시각은 실제 값을 알 수 없으므로 backfill하지 않고 NULL로 둔다
(이후 INSERT부터 DEFAULT CURRENT_TIMESTAMP로 기록됨).

이 스크립트는 위 검증이 끝났다는 전제로 곧바로 ALTER TABLE을 실행한다.
데이터가 변경된 이후 재실행 시에는 반드시 사전 검증부터 다시 해야 한다.

실행: python scripts/archive/type-drift-2026-07-29/fix_type_drift.py [--dry-run]
"""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

# 아카이브 하위 디렉터리에서 실행해도 app 패키지를 찾도록 저장소 루트를 경로에 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from sqlalchemy import text
from app.db.database import engine

# (설명, SQL)
STATEMENTS = [
    # ── DATETIME: text → DATETIME (+ ORM에 server_default이 선언된 컬럼은 DB 기본값 부여) ──
    ("products.created_at TEXT -> DATETIME DEFAULT CURRENT_TIMESTAMP",
     "ALTER TABLE products MODIFY created_at DATETIME DEFAULT CURRENT_TIMESTAMP"),
    ("products.updated_at TEXT -> DATETIME DEFAULT CURRENT_TIMESTAMP",
     "ALTER TABLE products MODIFY updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"),
    ("replacements.created_at TEXT -> DATETIME DEFAULT CURRENT_TIMESTAMP",
     "ALTER TABLE replacements MODIFY created_at DATETIME DEFAULT CURRENT_TIMESTAMP"),
    ("inventory.last_updated TEXT -> DATETIME DEFAULT CURRENT_TIMESTAMP",
     "ALTER TABLE inventory MODIFY last_updated DATETIME DEFAULT CURRENT_TIMESTAMP"),
    ("stock_alerts.sent_at TEXT -> DATETIME DEFAULT CURRENT_TIMESTAMP",
     "ALTER TABLE stock_alerts MODIFY sent_at DATETIME DEFAULT CURRENT_TIMESTAMP"),
    # resolved_at은 ORM에도 기본값이 없다 (해소 시점에만 기록)
    ("stock_alerts.resolved_at TEXT -> DATETIME NULL",
     "ALTER TABLE stock_alerts MODIFY resolved_at DATETIME NULL"),
    ("price_history.checked_at TEXT -> DATETIME DEFAULT CURRENT_TIMESTAMP",
     "ALTER TABLE price_history MODIFY checked_at DATETIME DEFAULT CURRENT_TIMESTAMP"),

    # ── 숫자: ORM 선언(Integer/Float)과 실제 타입 일치시키기 ──
    ("products.our_price DOUBLE -> BIGINT",
     "ALTER TABLE products MODIFY our_price BIGINT"),
    ("price_history.our_price TEXT -> BIGINT",
     "ALTER TABLE price_history MODIFY our_price BIGINT"),
    ("price_history.competitor_min TEXT -> BIGINT",
     "ALTER TABLE price_history MODIFY competitor_min BIGINT"),
    ("price_history.competitor_avg TEXT -> BIGINT",
     "ALTER TABLE price_history MODIFY competitor_avg BIGINT"),
    ("price_history.competitor_max TEXT -> BIGINT",
     "ALTER TABLE price_history MODIFY competitor_max BIGINT"),
    ("price_history.competitor_count TEXT -> BIGINT",
     "ALTER TABLE price_history MODIFY competitor_count BIGINT"),
    ("price_history.diff_percent TEXT -> DOUBLE",
     "ALTER TABLE price_history MODIFY diff_percent DOUBLE"),

    # ── BOOLEAN: text('0'/'1') → TINYINT(1) ──
    ("price_history.needs_adjustment TEXT -> TINYINT(1) DEFAULT 0",
     "ALTER TABLE price_history MODIFY needs_adjustment TINYINT(1) DEFAULT 0"),
    ("stock_alerts.resolved TEXT -> TINYINT(1) DEFAULT 0",
     "ALTER TABLE stock_alerts MODIFY resolved TINYINT(1) DEFAULT 0"),
]

VERIFY_SQL = """
    SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, COLUMN_DEFAULT
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND (TABLE_NAME, COLUMN_NAME) IN (
        ('products','created_at'), ('products','updated_at'), ('products','our_price'),
        ('replacements','created_at'), ('inventory','last_updated'),
        ('stock_alerts','sent_at'), ('stock_alerts','resolved_at'), ('stock_alerts','resolved'),
        ('price_history','checked_at'), ('price_history','our_price'),
        ('price_history','competitor_min'), ('price_history','competitor_avg'),
        ('price_history','competitor_max'), ('price_history','competitor_count'),
        ('price_history','diff_percent'), ('price_history','needs_adjustment')
      )
    ORDER BY TABLE_NAME, COLUMN_NAME
"""


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
                print("중단 — 남은 문장은 실행하지 않음. 원인 확인 후 재개할 것.")
                return

        if dry_run:
            return

        print("\n── 적용 후 컬럼 타입 확인 ──")
        for t, c, ctype, default in (await conn.execute(text(VERIFY_SQL))).all():
            print(f"  {t}.{c:<20} {ctype:<14} default={default}")

    print("\n완료 — 타입 드리프트 보정 적용됨.")


if __name__ == "__main__":
    asyncio.run(main(dry_run="--dry-run" in sys.argv))
