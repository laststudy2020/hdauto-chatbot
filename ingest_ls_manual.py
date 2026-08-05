"""LS 신형 인버터(S100/G100/H100) 매뉴얼 → DB 투입.

`upload_manual.py`(범용 CLOVA 파이프라인)와 달리 LLM을 안 거친다. 이 매뉴얼들은
페이지 선별과 표 구조가 특수해서 `app/services/ls_manual_parser`로 좌표 기반
결정적 추출을 한 뒤, 그 결과만 그대로 넣는다. 자세한 배경은 파서 docstring 참조.

사용법:
    python ingest_ls_manual.py --dry-run                 # 쓰지 않고 결과만 출력
    python ingest_ls_manual.py                           # manuals/ 아래 전부
    python ingest_ls_manual.py manuals/LSLV-S100.pdf S100

주의: `.env`의 DATABASE_URL이 프로덕션이면 그대로 프로덕션에 들어간다.
드라이런은 다음처럼 SQLite로 돌린다(환경변수가 .env보다 우선).
    $env:DATABASE_URL = "sqlite+aiosqlite:///./dryrun.db"
"""
import asyncio
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import select

from app.db.database import async_session, init_db
from app.db.models import AlarmCode, Product, ProductStatus, Specification
from app.services.ls_manual_parser import ParsedManual, parse_ls_manual

MANUFACTURER = "LS"
# (파일, 시리즈, OCR 필요 여부)
# G100만 OCR을 켠다. 이 PDF는 표의 영문·숫자가 텍스트 레이어에 있긴 해도 좌표가
# 실제 표 행과 전혀 다른 자리라, 렌더링 후 다시 읽어야 코드와 설명이 맞는다.
DEFAULT_TARGETS = [
    ("manuals/LSLV-S100.pdf", "S100", False),
    ("manuals/LSLV-G100.pdf", "G100", True),
    ("manuals/LSLV-H100.pdf", "H100", False),
]

# 컬럼 상한(app/db/models.py). 넘치면 DB가 자르거나 에러를 내므로 여기서 맞춘다.
MAX_CODE = 20
MAX_NAME = 100


async def _upsert_product(db, m, series_label: str) -> Product:
    prod = (await db.execute(
        select(Product).where(Product.model_name == m.model_name)
    )).scalar_one_or_none()
    if prod is None:
        prod = Product(
            model_name=m.model_name,
            series=series_label,
            manufacturer=MANUFACTURER,
            category="inverter",
            status=ProductStatus.ACTIVE,
        )
        db.add(prod)
        await db.flush()
    else:
        prod.series = series_label
        prod.manufacturer = MANUFACTURER
        prod.category = "inverter"
    return prod


async def _upsert_spec(db, prod: Product, m, series_label: str) -> str:
    spec = (await db.execute(
        select(Specification).where(Specification.product_id == prod.id)
    )).scalar_one_or_none()
    created = spec is None
    if created:
        spec = Specification(product_id=prod.id)
        db.add(spec)

    spec.dimension_w = m.dimension_w
    spec.dimension_h = m.dimension_h
    spec.dimension_d = m.dimension_d
    spec.weight_kg = m.weight_kg
    spec.input_voltage = m.voltage_class
    spec.rated_power = f"{m.capacity_kw}kW" if m.capacity_kw is not None else None
    spec.mounting_type = "DIN 레일/벽면"
    spec.catalog_page = str(m.spec_page) if m.spec_page else None
    spec.extra_specs = {
        "capacity_kw": m.capacity_kw,
        "rated_current_a": m.rated_current_a,
        "voltage_class": m.voltage_class,
        "series": series_label,
        "source": f"{MANUFACTURER}_{series_label}.pdf",
        "dimension_note": "W1/H1/D1 (mm)",
    }
    return "생성" if created else "갱신"


def _dedupe_alarms(alarms: list) -> list:
    """같은 코드가 한 매뉴얼 안에서 두 번 나오면 합친다.

    H100은 고장표와 경보표에 같은 LCD 명칭이 실린다(Over Load 등). 그냥 넣으면
    뒤에 오는 경보 설명이 앞의 고장 설명을 덮어써서 정작 트립 내용이 사라진다.
    """
    merged: dict[str, object] = {}
    for a in alarms:
        key = a.code[:MAX_CODE]
        prev = merged.get(key)
        if prev is None:
            merged[key] = a
            continue
        if a.state and a.state not in prev.state:
            prev.state = f"{prev.state}/{a.state}".strip("/")
        if a.description and a.description not in prev.description:
            prev.description = f"{prev.description} / {a.description}".strip(" /")
        for pair in a.actions:
            if pair not in prev.actions:
                prev.actions.append(pair)
    return list(merged.values())


async def _upsert_alarm(db, a, series_label: str) -> str:
    code = a.code[:MAX_CODE]
    row = (await db.execute(
        select(AlarmCode).where(
            AlarmCode.manufacturer == MANUFACTURER,
            AlarmCode.product_series == series_label,
            AlarmCode.alarm_code == code,
        )
    )).scalar_one_or_none()
    created = row is None
    if created:
        row = AlarmCode(
            manufacturer=MANUFACTURER,
            product_series=series_label,
            alarm_code=code,
        )
        db.add(row)

    label = f"{a.name} ({a.state})" if a.state else a.name
    row.alarm_name = label[:MAX_NAME]
    row.cause = a.description
    row.solution = a.solution_text()
    row.manual_page = str(a.page)
    row.manual_filename = f"LSLV-{series_label}.pdf"
    return "생성" if created else "갱신"


async def ingest(pdf_path: str, series: str, dry_run: bool,
                 use_ocr: bool = False) -> ParsedManual:
    series_label = f"LSLV-{series}"
    parsed = parse_ls_manual(Path(pdf_path).read_bytes(), series, use_ocr=use_ocr)
    raw_count = len(parsed.alarms)
    parsed.alarms = _dedupe_alarms(parsed.alarms)
    if raw_count != len(parsed.alarms):
        parsed.notes.append(
            f"중복 코드 {raw_count - len(parsed.alarms)}건 병합 (고장표+경보표 동일 명칭)"
        )

    print(f"\n=== {series_label} ({pdf_path}) ===")
    for n in parsed.notes:
        print(f"  [note] {n}")
    print(f"  파싱 결과: 알람 {len(parsed.alarms)}건, 모델 {len(parsed.models)}건")

    if not parsed.alarms and not parsed.models:
        print("  → 넣을 게 없어 건너뜁니다.")
        return parsed

    if dry_run:
        print("  → --dry-run: DB에 쓰지 않음")
        for a in parsed.alarms[:5]:
            print(f"     {a.code:<16} {a.name}")
        for m in parsed.models[:5]:
            print(f"     {m.model_name}  {m.capacity_kw}kW  {m.dimension_w}x"
                  f"{m.dimension_h}x{m.dimension_d}mm  {m.weight_kg}kg")
        return parsed

    stat = {"제품생성": 0, "제품갱신": 0, "스펙생성": 0, "스펙갱신": 0,
            "알람생성": 0, "알람갱신": 0}
    async with async_session() as db:
        for m in parsed.models:
            before = (await db.execute(
                select(Product.id).where(Product.model_name == m.model_name)
            )).scalar_one_or_none()
            prod = await _upsert_product(db, m, series_label)
            stat["제품갱신" if before else "제품생성"] += 1
            stat["스펙" + await _upsert_spec(db, prod, m, series_label)] += 1
        for a in parsed.alarms:
            stat["알람" + await _upsert_alarm(db, a, series_label)] += 1
        await db.commit()

    print("  → " + ", ".join(f"{k} {v}" for k, v in stat.items() if v))
    return parsed


async def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry_run = "--dry-run" in sys.argv

    if len(args) >= 2:
        targets = [(args[0], args[1], args[1].upper() == "G100")]
    elif args:
        print("사용법: python ingest_ls_manual.py [PDF경로] [S100|G100|H100] [--dry-run]")
        return
    else:
        targets = DEFAULT_TARGETS

    missing = [p for p, _s, _o in targets if not os.path.exists(p)]
    if missing:
        print(f"파일 없음: {missing}")
        return

    if not dry_run:
        await init_db()

    for pdf_path, series, use_ocr in targets:
        await ingest(pdf_path, series, dry_run, use_ocr=use_ocr)


if __name__ == "__main__":
    asyncio.run(main())
