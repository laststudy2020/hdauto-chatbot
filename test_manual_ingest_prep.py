"""LS 신형 인버터(S100/G100/H100) 매뉴얼 투입 준비 검증.

프로덕션 DB를 건드리지 않는다 — 중복판정 검증은 임시 SQLite에 스키마를 새로
만들어서 한다. CLOVA 호출도 없다(추출 함수와 중복판정만 검증).

실행: python test_manual_ingest_prep.py
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import AlarmCode, Base
from app.services.pdf_processor import (
    _alarm_exists, _extract_model_names, _infer_category,
)

# 매뉴얼 표지/형명 표기에서 실제로 뽑혀야 하는 모델명
MODEL_CASES = [
    ("S100 형명표", "형명: LSLV0022S100-4EOFNM (2.2kW 3상 400V)", "LSLV0022S100-4EOFNM"),
    ("S100 단상", "LSLV0008S100-1EOFNM 사양", "LSLV0008S100-1EOFNM"),
    ("G100 형명표", "정격 LSLV0015G100-4EOFN 입력전압 380~480V", "LSLV0015G100-4EOFN"),
    ("H100 형명표", "LSLV0022H100-4COFN 팬/펌프 전용", "LSLV0022H100-4COFN"),
    ("기존 iG5A 회귀", "SV008iG5A-4 표준형", "SV008IG5A-4"),
]

CATEGORY_CASES = [
    ("S100", "LSLV0022S100-4EOFNM", "LSLV-S100", "inverter"),
    ("G100", "LSLV0015G100-4EOFN", "LSLV-G100", "inverter"),
    ("H100", "LSLV0022H100-4COFN", "LSLV-H100", "inverter"),
    ("iG5A 회귀", "SV008IG5A-4", "SV-iG5A", "inverter"),
    ("MR-J4 회귀", "MR-J4-70A", "MELSERVO-J4", "servo"),
]


def test_model_extraction() -> list[str]:
    print("=" * 78)
    print("[1] 모델명 추출 — 신형 LS 인버터 형명")
    print("=" * 78)
    fails = []
    for desc, text, expected in MODEL_CASES:
        found = _extract_model_names(text, "LS")
        ok = expected in found
        print(f"  {'✔' if ok else '✘'} {desc:16} {expected:22} → {found}")
        if not ok:
            fails.append(f"{desc}: {expected} 미추출 (실제 {found})")
    return fails


def test_category() -> list[str]:
    print("\n" + "=" * 78)
    print("[2] 카테고리 판정")
    print("=" * 78)
    fails = []
    for desc, model, series, expected in CATEGORY_CASES:
        actual = _infer_category(model, series)
        ok = actual == expected
        print(f"  {'✔' if ok else '✘'} {desc:12} {series:14} → {actual} ({expected} 기대)")
        if not ok:
            fails.append(f"{desc}: 카테고리 {expected} 기대 → {actual}")
    return fails


async def test_dedup_scope() -> list[str]:
    """같은 제조사·같은 코드라도 시리즈가 다르면 별도 행으로 들어가야 한다."""
    print("\n" + "=" * 78)
    print("[3] 알람코드 중복판정 범위 — 시리즈가 다르면 별도 등록")
    print("=" * 78)

    fails = []
    tmp = Path(tempfile.gettempdir()) / "hdauto_ingest_prep_test.db"
    tmp.unlink(missing_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as db:
            # 기존 iG5A 코드가 이미 있는 상태를 재현
            db.add(AlarmCode(
                manufacturer="LS", product_series="SV-iG5A", alarm_code="OCt",
                alarm_name="과전류", cause="...", solution="...",
            ))
            await db.commit()

            same = await _alarm_exists(db, "LS", "SV-iG5A", "OCt")
            other = await _alarm_exists(db, "LS", "LSLV-S100", "OCt")
            new_code = await _alarm_exists(db, "LS", "LSLV-S100", "GFt")

            print(f"  {'✔' if same else '✘'} 같은 시리즈 같은 코드 → 중복으로 판정 (True 기대): {same}")
            print(f"  {'✔' if not other else '✘'} 다른 시리즈 같은 코드 → 신규로 판정 (False 기대): {other}")
            print(f"  {'✔' if not new_code else '✘'} 다른 시리즈 새 코드 → 신규로 판정 (False 기대): {new_code}")

            if not same:
                fails.append("같은 시리즈 중복이 걸러지지 않음")
            if other:
                fails.append("시리즈가 달라도 중복으로 스킵됨 — 신형 시리즈 코드가 유실된다")
            if new_code:
                fails.append("새 코드가 중복으로 판정됨")
    finally:
        await engine.dispose()
        tmp.unlink(missing_ok=True)

    return fails


async def main():
    fails = test_model_extraction()
    fails += test_category()
    fails += await test_dedup_scope()

    print("\n" + "=" * 78)
    if fails:
        print(f"실패 {len(fails)}건")
        for f in fails:
            print(f"  · {f}")
    else:
        print("전체 통과 — S100/G100/H100 매뉴얼 투입 준비 완료")
    print("=" * 78)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
