"""P0 3건 검증 — 대체품 형명 고정 / 알람 시리즈 필터 / 재고 중고 표기.

pytest 아님. `python test_p0_fixes.py`로 직접 실행한다.
CLOVA는 스텁으로 갈아끼워, 프롬프트에 실제로 뭐가 실려 가는지까지 들여다본다.
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.clova import clova_client
from app.db.database import async_session
from app.db.models import AlarmCode, Product
from app.services import alarm as alarm_svc
from app.services import inventory as inv_svc
from app.services import replacement as rep_svc

passed = failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}   {detail}")


class _Row:
    """AlarmCode 흉내 — 필터 로직만 보므로 필요한 필드만 둔다."""

    def __init__(self, code, series, solution=""):
        self.alarm_code = code
        self.alarm_name = code
        self.product_series = series
        self.manufacturer = "LS"
        self.cause = "원인"
        self.solution = solution
        self.manual_filename = "m.pdf"
        self.manual_page = 1


class _Stub:
    """CLOVA 대신 프롬프트를 그대로 돌려주거나, 형명을 일부러 뭉갠다."""

    def __init__(self, mode="echo"):
        self.mode = mode
        self.last_prompt = ""

    async def __call__(self, system_prompt="", user_message="", temperature=0.0, **kw):
        self.last_prompt = user_message
        if self.mode == "paraphrase":
            # 프로덕션에서 실제로 나온 형태: 형명이 시리즈명으로 소실된 답변
            return "'SV004iG5A-1' 단종품의 대체품으로는 LS산전의 LSLV-S100 시리즈를 추천드립니다."
        return user_message


# ────────────────────────────────────────────────────────────
# [1] 대체품 형명 고정 문구 (단위)
# ────────────────────────────────────────────────────────────
def test_exact_model_note():
    print("\n[1] _build_exact_model_note")

    class R:
        def __init__(self, name):
            self.new_product = type("P", (), {"model_name": name})()

    note = rep_svc._build_exact_model_note([R("LSLV0004S100-1")])
    check("정확한 형명 포함", "LSLV0004S100-1" in note, note)
    check("단상 200V 설명", "-1 단상 200V" in note, note)

    note2 = rep_svc._build_exact_model_note([R("MR-J4-10A")])
    check("MR-J4 형명 포함", "MR-J4-10A" in note2, note2)
    check("LS 접미사 규칙을 남의 제품군에 붙이지 않음",
          "전압·상 구분" not in note2, note2)

    check("매핑 없으면 빈 문자열", rep_svc._build_exact_model_note([]) == "")


# ────────────────────────────────────────────────────────────
# [2] 알람 시리즈 필터 (단위)
# ────────────────────────────────────────────────────────────
def test_select_series():
    print("\n[2] _select_series")

    known = {"G100", "H100", "S100", "IG5A"}
    rows = [
        _Row("OLT", "LSLV-H100", "H100 조치"),
        _Row("OLT", "LSLV-G100", ""),
        _Row("OLT", "LSLV-S100", "S100 조치"),
    ]

    got, ident = alarm_svc._select_series(rows, "LSLV0022G100-2 OLT 알람", known)
    check("G100 질문 → G100만 남음",
          ident and [r.product_series for r in got] == ["LSLV-G100"],
          [r.product_series for r in got])
    check("H100 행이 제거됨", all(r.product_series != "LSLV-H100" for r in got))

    got2, ident2 = alarm_svc._select_series(rows, "OLT 알람이 떴어요", known)
    check("시리즈 미특정 → 전부 유지, identified=False",
          (not ident2) and len(got2) == 3, (ident2, len(got2)))

    only_h = [_Row("OV", "LSLV-H100", "H100 조치")]
    got3, ident3 = alarm_svc._select_series(only_h, "LSLV0022G100-2 OV 알람", known)
    check("G100 질문인데 G100 행 없음 → 빈 결과(형제 시리즈 안 씀)",
          ident3 and got3 == [], (ident3, len(got3)))


# ────────────────────────────────────────────────────────────
# [3] diagnose_alarm — 실제 DB 프롬프트 내용 검사
# ────────────────────────────────────────────────────────────
async def test_diagnose_alarm(db):
    print("\n[3] diagnose_alarm (실 DB)")

    stub = _Stub("echo")
    orig = clova_client.chat_completion
    clova_client.chat_completion = stub
    try:
        # G100에 실제로 있는 코드를 하나 집는다
        row = (await db.execute(
            select(AlarmCode).where(AlarmCode.product_series.ilike("%G100%"))
        )).scalars().first()
        if not row:
            print("  SKIP  G100 알람 행이 없음")
            clova_client.chat_completion = orig
            return
        code = row.alarm_code
        print(f"  대상 코드: {code}")

        reply, matched = await alarm_svc.diagnose_alarm(
            alarm_code=code,
            model_name="LSLV0022G100-2",
            user_message=f"LSLV0022G100-2 에서 {code} 알람이 떴어요",
            db=db,
        )
        check("DB 매칭됨", matched)
        check("G100 근거가 실림", "G100" in reply, reply[:200])
        check("H100 근거가 안 실림", "H100" not in reply,
              [l for l in reply.splitlines() if "H100" in l][:3])
        check("S100 근거가 안 실림", "S100" not in reply,
              [l for l in reply.splitlines() if "S100" in l][:3])

        # 존재하지 않는 코드 → 형제 시리즈로 때우지 말 것
        reply2, matched2 = await alarm_svc.diagnose_alarm(
            alarm_code="ZZZ9",
            model_name="LSLV0022G100-2",
            user_message="LSLV0022G100-2 에서 ZZZ9 알람이 떴어요",
            db=db,
        )
        check("없는 코드 → DB 미매칭 처리", not matched2)

        # 진짜 위험한 경우: 형제 시리즈에만 있는 코드를 G100으로 물어본다
        g100_codes = set((await db.execute(
            select(AlarmCode.alarm_code)
            .where(AlarmCode.product_series.ilike("%G100%"))
        )).scalars().all())
        sibling = (await db.execute(
            select(AlarmCode)
            .where(AlarmCode.product_series.ilike("%H100%"))
        )).scalars().all()
        only_h100 = next((a for a in sibling if a.alarm_code not in g100_codes), None)
        if only_h100:
            print(f"  H100 전용 코드: {only_h100.alarm_code}")
            reply3, matched3 = await alarm_svc.diagnose_alarm(
                alarm_code=only_h100.alarm_code,
                model_name="LSLV0022G100-2",
                user_message=f"LSLV0022G100-2 에서 {only_h100.alarm_code} 알람이 떴어요",
                db=db,
            )
            check("H100 전용 코드를 G100으로 물으면 H100 근거를 쓰지 않음",
                  "H100" not in reply3,
                  [l for l in reply3.splitlines() if "H100" in l][:3])
            check("매뉴얼에 없다고 안내하도록 지시됨",
                  "코드가 없습니다" in reply3, reply3[:200])
        else:
            print("  SKIP  H100 전용 코드를 찾지 못함")
    finally:
        clova_client.chat_completion = orig


# ────────────────────────────────────────────────────────────
# [4] find_replacement — CLOVA가 형명을 뭉개도 정답 형명이 남는가
# ────────────────────────────────────────────────────────────
async def test_replacement_exact_name(db):
    print("\n[4] find_replacement (CLOVA 형명 소실 재현)")

    stub = _Stub("paraphrase")
    orig = clova_client.chat_completion
    clova_client.chat_completion = stub
    try:
        reply, matched = await rep_svc.find_replacement("SV004iG5A-1", db)
        check("DB 매칭됨", matched)
        check("CLOVA 답변은 여전히 시리즈명으로 뭉갬(재현 확인)",
              "LSLV-S100 시리즈" in reply)
        check("그래도 정확한 형명이 응답에 남음",
              "LSLV0004S100-1" in reply, reply[-400:])
        check("3상 형명을 잘못 붙이지 않음", "LSLV0004S100-2" not in reply)
        print("  ---- 응답 꼬리 ----")
        print("  " + reply[-320:].replace("\n", "\n  "))
    finally:
        clova_client.chat_completion = orig


# ────────────────────────────────────────────────────────────
# [5] 재고 경로 중고 표기
# ────────────────────────────────────────────────────────────
async def test_inventory_used(db):
    print("\n[5] get_inventory_status 중고 표기")

    # stock_condition='used'가 찍힌 제품을 실제로 하나 고른다
    prods = (await db.execute(
        select(Product).options(selectinload(Product.specs),
                                selectinload(Product.inventory))
    )).scalars().all()
    used = [p for p in prods if inv_svc._is_used_stock(p)]
    check("중고 표기 대상 제품이 DB에 있음", bool(used), len(used))
    if not used:
        return

    target = next((p for p in used
                   if p.inventory and (p.inventory.current_stock or 0) > 0), None)
    if not target:
        print("  SKIP  중고 표기 대상 중 DB 재고>0인 제품 없음")
        return

    print(f"  대상: {target.model_name}")
    reply = await inv_svc.get_inventory_status(target.model_name, db)
    check("'중고 재고 있음' 표기", "중고 재고 있음" in reply,
          reply.splitlines()[0] if reply else "")
    check("보유분이 중고임을 명시", "보유분은 중고" in reply)

    # 중고 표기가 없는 정상 판매품은 문구가 바뀌지 않아야 한다
    normal = next((p for p in prods
                   if not inv_svc._is_used_stock(p)
                   and p.status.value == "active"), None)
    if normal:
        r2 = await inv_svc.get_inventory_status(normal.model_name, db)
        check(f"정상품({normal.model_name})에 중고 문구 없음",
              "중고" not in r2, r2[:160])


# ────────────────────────────────────────────────────────────
# [6] 재고 응답에 생성된 대체품이 섞이지 않는가
# ────────────────────────────────────────────────────────────
async def test_inventory_no_web_replacement(db):
    print("\n[6] 재고 응답 — 웹/LLM 생성 대체품 미혼입")

    check("inventory가 search_and_answer를 import하지 않음",
          not hasattr(inv_svc, "search_and_answer"))

    # 단종 + DB 대체품이 등록된 제품으로 확인한다
    prods = (await db.execute(
        select(Product).options(selectinload(Product.specs),
                                selectinload(Product.inventory))
    )).scalars().all()
    target = next((p for p in prods
                   if p.status.value == "discontinued"
                   and p.model_name == "SV015iG5A-4"), None)
    if not target:
        target = next((p for p in prods if p.status.value == "discontinued"), None)
    if not target:
        print("  SKIP  단종 제품이 없음")
        return

    print(f"  대상: {target.model_name}")
    reply = await inv_svc.get_inventory_status(target.model_name, db)
    check("'유사 사양 제품 안내' 섹션이 사라짐", "유사 사양 제품 안내" not in reply,
          reply[:200])
    check("DB 대체품 안내는 유지", "추천 대체 모델" in reply, reply[:200])
    # 프로덕션에서 실제로 나왔던 환각 형명
    check("환각 형명(iS7/S130IS7)이 안 나옴",
          "IS7" not in reply.upper(),
          [l for l in reply.splitlines() if "IS7" in l.upper()][:3])


async def main():
    async with async_session() as db:
        test_exact_model_note()
        test_select_series()
        await test_diagnose_alarm(db)
        await test_replacement_exact_name(db)
        await test_inventory_used(db)
        await test_inventory_no_web_replacement(db)

    print(f"\n{'=' * 60}\n결과: {passed} PASS / {failed} FAIL\n{'=' * 60}")
    return 1 if failed else 0


sys.exit(asyncio.run(main()))
