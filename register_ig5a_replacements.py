"""iG5A 단종 처리 + 용량·전압 일치 기준 후계 인버터 자동 매핑.

SV-iG5A는 신품 단종이지만 중고는 계속 판매 중이다. 그래서 세 가지를 같이 한다.

  1) iG5A 전 모델 status -> DISCONTINUED (신품 기준 단종이 사실)
  2) 실제로 팔 수 있는 건(스토어 등록됐거나 재고 보유)에 중고 표기
     -> Specification.extra_specs["stock_condition"] = "used"
  3) 정격 kW + 전압등급 + 상(相)이 모두 일치하는 후계 모델을 대체품으로 등록

매칭 우선순위는 G100 > S100 > H100. iG5A의 직계 후계가 범용기 G100이고, H100은
팬·펌프 전용이라 일반 동력용으로 먼저 권하면 안 된다. 조건에 맞는 것 중 하나만
등록한다.

상(相)을 반드시 맞춘다. 전압등급만 보면 SV004iG5A-1(단상 200V)에 LSLV0004G100-2
(3상 200V)가 걸린다. 단상 전원 현장에 3상 인버터를 권하는 건 현장 사고다.

호환 플래그(단자대/외형/프로그램)는 NULL로 둔다. 용량·전압만 본 자동 매핑이라
호환을 검증한 적이 없다. O로 찍으면 없는 호환을 지어내고 X로 찍으면 확인도 안 한
비호환을 단정하게 된다.

  python register_ig5a_replacements.py           # dry-run, 매핑표만 출력
  python register_ig5a_replacements.py --apply   # 실제 반영

재실행해도 안전하다(이미 있는 (old,new) 쌍과 이미 반영된 status는 건너뛴다).
"""

import argparse
import asyncio
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.database import async_session
from app.db.models import (
    Product, ProductStatus, Replacement, Specification,
)
from app.services.spec_search import (
    INVERTER_CATEGORIES, _product_voltage_classes, _rated_kw,
)

# 후계 시리즈 우선순위. 낮을수록 먼저 고른다.
SUCCESSOR_PRIORITY = {"G100": 0, "S100": 1, "H100": 2}

NOTE = ("용량·전압 사양 일치 기준 자동 매핑 — 단자대·외형·프로그램 호환은 "
        "미검증이므로 적용 전 확인 필요")

# 형명 끝의 -1/-2/-4가 상(相)과 전압을 확정한다. iG5A와 LSLV가 같은 규칙을 쓴다.
_SUFFIX = re.compile(r"-(\d)$")
_SUFFIX_MEANING = {"1": "단상 200V", "2": "3상 200V", "4": "3상 400V"}


def suffix_of(model_name: str) -> str | None:
    m = _SUFFIX.search(model_name or "")
    return m.group(1) if m else None


def successor_series(model_name: str) -> str | None:
    for label in SUCCESSOR_PRIORITY:
        if label.lower() in (model_name or "").lower():
            return label
    return None


def is_inverter(p: Product) -> bool:
    cat = (p.category or "").lower()
    return any(c in cat or c in (p.category or "") for c in INVERTER_CATEGORIES)


def match_key(p: Product) -> tuple | None:
    """(정격kW, 전압등급, 상/전압 접미사). 하나라도 확정 못 하면 None."""
    kw = _rated_kw(p.specs.rated_power if p.specs else None)
    classes = _product_voltage_classes(p)
    sfx = suffix_of(p.model_name)
    if kw is None or len(classes) != 1 or sfx is None:
        return None
    return (kw, next(iter(classes)), sfx)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 DB에 반영")
    args = ap.parse_args()

    async with async_session() as db:
        rows = (await db.execute(
            select(Product).options(
                selectinload(Product.specs), selectinload(Product.inventory)
            )
        )).scalars().all()
        inverters = [p for p in rows if is_inverter(p)]

        olds = sorted([p for p in inverters if "ig5a" in p.model_name.lower()],
                      key=lambda p: p.model_name)
        news = [p for p in inverters if successor_series(p.model_name)]

        # 후계 카탈로그를 매칭키로 색인
        index: dict[tuple, list[Product]] = {}
        for p in news:
            key = match_key(p)
            if key:
                index.setdefault(key, []).append(p)

        existing = {
            (r.old_model_id, r.new_model_id)
            for r in (await db.execute(select(Replacement))).scalars().all()
        }

        print("=" * 78)
        print(f"iG5A {len(olds)}건 / 후계 카탈로그 {len(news)}건 / 색인키 {len(index)}개")
        print(f"모드: {'APPLY (실제 반영)' if args.apply else 'DRY-RUN (출력만)'}")
        print("=" * 78)

        planned, skipped, unmatched, nokey = [], [], [], []

        print(f"\n{'구모델':<16}{'kW':>6} {'전원':<11} -> {'대체품':<18}비고")
        print("-" * 78)
        for p in olds:
            key = match_key(p)
            if key is None:
                nokey.append(p)
                print(f"{p.model_name:<16}{'?':>6} {'?':<11} -> (키 확정 실패)")
                continue

            kw, vclass, sfx = key
            power = _SUFFIX_MEANING.get(sfx, sfx)
            cands = sorted(
                index.get(key, []),
                key=lambda c: (SUCCESSOR_PRIORITY[successor_series(c.model_name)],
                               c.model_name),
            )
            if not cands:
                unmatched.append(p)
                print(f"{p.model_name:<16}{kw:>6} {power:<11} -> (해당 사양 후계 없음)")
                continue

            pick = cands[0]
            others = [c.model_name for c in cands[1:]]
            tag = f"(다른 후보: {', '.join(others)})" if others else ""
            if (p.id, pick.id) in existing:
                skipped.append((p, pick))
                print(f"{p.model_name:<16}{kw:>6} {power:<11} -> {pick.model_name:<18}이미 등록")
            else:
                planned.append((p, pick))
                print(f"{p.model_name:<16}{kw:>6} {power:<11} -> {pick.model_name:<18}{tag}")

        # ── 단종 표기 / 중고 표기 대상 산정 ──
        to_discontinue = [p for p in olds if p.status != ProductStatus.DISCONTINUED]
        # 실제로 팔 수 있는 것만 중고로 표기한다. 스토어에 올라가 있거나, 등록은 없어도
        # 재고를 들고 있는 건(SV015iG5A-4 같은 경우) 파는 물건이므로 포함한다.
        sellable = [
            p for p in olds
            if p.smartstore_product_id
            or (p.inventory and (p.inventory.current_stock or 0) > 0)
        ]
        to_mark_used = [
            p for p in sellable
            if not (p.specs and (p.specs.extra_specs or {}).get("stock_condition") == "used")
        ]

        print("-" * 78)
        print(f"신규 매핑 {len(planned)} / 이미등록 {len(skipped)} / "
              f"후계없음 {len(unmatched)} / 키실패 {len(nokey)}")
        print(f"단종 전환 대상 {len(to_discontinue)}건 / "
              f"중고 표기 대상 {len(to_mark_used)}건 (판매가능 {len(sellable)}건)")

        if unmatched:
            print(f"\n[후계 없음 — 수동 확인 필요]")
            for p in unmatched:
                k = match_key(p)
                print(f"  {p.model_name}  {k[0]}kW {_SUFFIX_MEANING.get(k[2], k[2])}")

        if not args.apply:
            print("\n반영하려면 --apply 를 붙여 다시 실행하세요.")
            return

        # ── 반영 ──
        for p in to_discontinue:
            p.status = ProductStatus.DISCONTINUED

        for p in to_mark_used:
            spec = p.specs
            if spec is None:
                spec = Specification(product_id=p.id, extra_specs={})
                db.add(spec)
                await db.flush()
            extra = dict(spec.extra_specs or {})
            extra["stock_condition"] = "used"
            # JSON 컬럼은 제자리 수정하면 변경 감지가 안 된다. 새 dict를 대입한다.
            spec.extra_specs = extra

        # 이 스크립트가 만든 행의 호환 플래그를 '미확인'(NULL)으로 정규화한다.
        # models.Replacement의 default=False가 명시적 None을 덮어써 26건이 False로
        # 들어간 적이 있다(default 제거로 고쳤지만, 이미 들어간 행은 여기서 되돌린다).
        auto_rows = (await db.execute(
            select(Replacement).where(Replacement.compatibility_notes == NOTE)
        )).scalars().all()
        repaired = 0
        for r in auto_rows:
            if (r.terminal_compatible is not None
                    or r.program_convertible is not None
                    or r.dimension_compatible is not None):
                r.terminal_compatible = None
                r.program_convertible = None
                r.dimension_compatible = None
                repaired += 1

        for old, new in planned:
            db.add(Replacement(
                old_model_id=old.id,
                new_model_id=new.id,
                compatibility_notes=NOTE,
                # 컬럼 default가 False라 명시적으로 None을 넣어야 '미확인'이 된다.
                terminal_compatible=None,
                program_convertible=None,
                dimension_compatible=None,
            ))

        await db.commit()
        print(f"\n반영 완료: 매핑 {len(planned)}건 추가 / "
              f"단종 전환 {len(to_discontinue)}건 / 중고 표기 {len(to_mark_used)}건 / "
              f"호환플래그 미확인 정규화 {repaired}건")


if __name__ == "__main__":
    asyncio.run(main())
