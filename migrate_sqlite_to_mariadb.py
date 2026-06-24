"""
SQLite -> MariaDB 마이그레이션 스크립트
hdauto-chatbot 프로젝트의 기존 SQLite DB(제품/대체품/스펙/재고/알람코드)를
NAS(DS225+)에 띄운 MariaDB로 옮긴다.

사용법:
    pip install pandas sqlalchemy pymysql --break-system-packages
    python migrate_sqlite_to_mariadb.py

환경변수 (.env 또는 직접 export):
    SQLITE_PATH      : 기존 SQLite 파일 경로 (예: ./hdauto.db)
    MARIADB_HOST      : NAS IP 또는 Tailscale IP (예: 100.x.x.x)
    MARIADB_PORT      : 기본 3306
    MARIADB_USER      : docker-compose.yml의 MYSQL_USER
    MARIADB_PASSWORD  : docker-compose.yml의 MYSQL_PASSWORD
    MARIADB_DB        : 기본 hdauto_chatbot
"""

import os
import sys
import sqlite3
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

# ---- 설정 -------------------------------------------------------------
SQLITE_PATH = os.getenv("SQLITE_PATH", "./hdauto.db")
MARIADB_HOST = os.getenv("MARIADB_HOST", "127.0.0.1")
MARIADB_PORT = os.getenv("MARIADB_PORT", "3306")
MARIADB_USER = os.getenv("MARIADB_USER", "hdauto")
MARIADB_PASSWORD = os.getenv("MARIADB_PASSWORD", "")
MARIADB_DB = os.getenv("MARIADB_DB", "hdauto_chatbot")

# 마이그레이션에서 제외할 테이블 (sqlite 내부 시스템 테이블 등)
SKIP_TABLES = {"sqlite_sequence", "sqlite_stat1"}

# 테이블별 예상 행 수 (검증용 — 실제 스키마에 맞게 수정하세요)
EXPECTED_ROWS = {
    "products": 28,
    "replacements": 10,
    "specs": 14,
    "inventory": 16,
    "alarm_codes": 84,
}


def get_sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return [row[0] for row in cur.fetchall() if row[0] not in SKIP_TABLES]


def migrate():
    if not os.path.exists(SQLITE_PATH):
        sys.exit(f"[오류] SQLite 파일을 찾을 수 없습니다: {SQLITE_PATH}")

    print(f"SQLite 연결: {SQLITE_PATH}")
    sqlite_conn = sqlite3.connect(SQLITE_PATH)

    # URL.create를 쓰면 비밀번호에 @, #, : 같은 특수문자가 있어도
    # 자동으로 안전하게 인코딩되어 연결 주소가 깨지지 않는다.
    mariadb_url = URL.create(
        drivername="mysql+pymysql",
        username=MARIADB_USER,
        password=MARIADB_PASSWORD,
        host=MARIADB_HOST,
        port=int(MARIADB_PORT),
        database=MARIADB_DB,
        query={"charset": "utf8mb4"},
    )
    print(f"MariaDB 연결: {MARIADB_HOST}:{MARIADB_PORT}/{MARIADB_DB}")
    engine = create_engine(mariadb_url)

    # 연결 확인
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print("MariaDB 연결 확인 완료\n")

    tables = get_sqlite_tables(sqlite_conn)
    if not tables:
        sys.exit("[오류] SQLite에서 테이블을 찾을 수 없습니다.")

    print(f"발견된 테이블 ({len(tables)}개): {', '.join(tables)}\n")

    summary = {}
    for table in tables:
        df = pd.read_sql_query(f"SELECT * FROM {table}", sqlite_conn)
        row_count = len(df)

        # 기존 테이블을 지우고 새로 생성 (재실행 시 중복 방지)
        df.to_sql(
            table,
            engine,
            if_exists="replace",
            index=False,
            chunksize=500,
        )
        summary[table] = row_count
        print(f"  [완료] {table}: {row_count}행 이전")

    print("\n--- 검증 ---")
    all_ok = True
    for table, expected in EXPECTED_ROWS.items():
        actual = summary.get(table)
        if actual is None:
            print(f"  [건너뜀] {table}: SQLite에 해당 테이블 없음")
            continue
        mark = "OK" if actual == expected else "확인 필요"
        if actual != expected:
            all_ok = False
        print(f"  {table}: 예상 {expected}행 / 실제 {actual}행 -> {mark}")

    sqlite_conn.close()
    print("\n마이그레이션 완료" + (" (전체 검증 통과)" if all_ok else " (일부 행 수 불일치, 확인 필요)"))


if __name__ == "__main__":
    migrate()