"""iG5A 단종 대체품 매핑 + 단종품 재고 안내 검증.

단종 표기를 켜면 대체품 분기가 열리는 대신, 우리가 실제로 갖고 파는 중고 재고를
답변에서 놓칠 수 있다(iG5A 22건이 스마트스토어에 올라가 있다). 그 두 가지가 한
답변에 같이 나오는지를 본다.

  python test_ig5a_replacement.py

register_ig5a_replacements.py --apply 를 먼저 돌린 상태를 전제한다.
CLOVA는 스텁으로 막는다(무료). 재고는 실제 조회를 그대로 태운다.
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core import clova as clova_mod
from app.db.database import async_session
from app.db.models import Product, ProductStatus, Replacement

_real_chat = clova_mod.clova_client.chat_completion


async def _stub_chat(system_prompt: str, user_message: str, **kwargs) -> str:
    # 대체품 분기의 RAG 컨텍스트가 그대로 흘러들어오는지 보려고 원문을 돌려준다.
    return f"[STUB]\n{user_message}"


passed = failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}   {detail}")


async def main() -> None:
    clova_mod.clova_client.chat_completion = _stub_chat
    from app.services.replacement import find_replacement, _build_context

    async with async_session() as db:
        # ── 1) 지목 매핑이 등록됐는가 ──
        print("\n[1] SV008iG5A-4 -> LSLV0008G100-4 매핑")
        old = (await db.execute(
            select(Product).where(Product.model_name == "SV008iG5A-4")
        )).scalars().first()
        check("SV008iG5A-4 제품 존재", old is not None)
        if old:
            check("단종 표기됨", old.status == ProductStatus.DISCONTINUED,
                  f"실제={old.status.value}")
            reps = (await db.execute(
                select(Replacement)
                .options(selectinload(Replacement.new_product))
                .where(Replacement.old_model_id == old.id)
            )).scalars().all()
            targets = [r.new_product.model_name for r in reps]
            check("대체품 LSLV0008G100-4 등록", "LSLV0008G100-4" in targets,
                  f"실제={targets}")
            check("대체품 1건만 등록", len(reps) == 1, f"실제={len(reps)}건")
            if reps:
                r = reps[0]
                check("호환 플래그는 미확인(NULL)",
                      r.terminal_compatible is None
                      and r.dimension_compatible is None
                      and r.program_convertible is None,
                      f"실제={r.terminal_compatible}/{r.dimension_compatible}"
                      f"/{r.program_convertible}")
                check("근거 비고 기재", bool(r.compatibility_notes)
                      and "자동" in (r.compatibility_notes or ""),
                      f"실제={r.compatibility_notes!r}")

        # ── 2) 미확인 플래그를 '비호환'으로 단정하지 않는가 ──
        print("\n[2] 미확인 호환 플래그 렌더링")
        if old:
            reps = (await db.execute(
                select(Replacement)
                .options(selectinload(Replacement.new_product)
                         .selectinload(Product.specs))
                .where(Replacement.old_model_id == old.id)
            )).scalars().all()
            ctx = _build_context(old, reps)
            # '단종일: 미확인' 줄에 걸려 위양성이 나지 않도록 플래그 줄을 콕 집는다.
            check("단자대 호환을 '미확인'으로 표기",
                  "단자대 호환: 미확인" in ctx, ctx[:300])
            check("외형 호환을 '미확인'으로 표기",
                  "외형 호환: 미확인" in ctx, ctx[:300])
            check("'단자대 호환: X'로 단정하지 않음",
                  "단자대 호환: X" not in ctx, ctx[:300])

        # ── 3) 단종이어도 보유 재고를 답변에 알리는가 ──
        print("\n[3] 단종 + 재고 보유 응답")
        reply, matched = await find_replacement("SV008iG5A-4", db)
        print("  ---- 응답 ----")
        for line in reply.splitlines():
            print(f"  | {line}")
        print("  --------------")
        check("DB 매칭됨", matched is True)
        check("대체품 형명 포함", "LSLV0008G100-4" in reply)
        check("보유 재고 안내 포함", "재고" in reply)
        check("중고임을 밝힘", "중고" in reply)
        check("구매 경로 안내", "smartstore" in reply.lower())

        # ── 4) 재고 없는 단종품은 중고 문구가 나가지 않는가 ──
        print("\n[4] 재고 없는 단종품 (SV220iG5A-4, 스토어 미등록)")
        reply2, _ = await find_replacement("SV220iG5A-4", db)
        print(f"  {reply2[:180].replace(chr(10), ' | ')}")
        check("중고 재고 문구 없음", "중고 재고" not in reply2)

        # ── 5) 상(相) 구분 — 단상을 3상으로 대체하지 않는가 ──
        print("\n[5] 단상/3상 구분")
        singles = (await db.execute(
            select(Product)
            .options(selectinload(Product.old_replacements)
                     .selectinload(Replacement.new_product))
            .where(Product.model_name.like("SV%iG5A-1"))
        )).scalars().all()
        bad = []
        for p in singles:
            for r in p.old_replacements:
                name = r.new_product.model_name
                if not name.endswith("-1"):
                    bad.append(f"{p.model_name}->{name}")
        check("단상(-1)은 단상(-1)으로만 매핑", not bad, f"위반={bad}")

        # 400V(-4)가 200V로 새지 않는지도 같이 본다
        v4 = (await db.execute(
            select(Product)
            .options(selectinload(Product.old_replacements)
                     .selectinload(Replacement.new_product))
            .where(Product.model_name.like("SV%iG5A-4"))
        )).scalars().all()
        bad4 = []
        for p in v4:
            for r in p.old_replacements:
                name = r.new_product.model_name
                if not name.endswith("-4"):
                    bad4.append(f"{p.model_name}->{name}")
        check("400V(-4)는 400V(-4)로만 매핑", not bad4, f"위반={bad4}")

    clova_mod.clova_client.chat_completion = _real_chat
    print(f"\n{'=' * 66}\n결과: {passed} PASS / {failed} FAIL\n{'=' * 66}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
