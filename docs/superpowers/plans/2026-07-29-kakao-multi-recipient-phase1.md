# 재고 알림 다중 수신자 1단계 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 재고 알림을 활성 수신자 전원에게 발송하고, 경쟁사 단가에서 해외 상품을 제외하며, 카탈로그 미매칭 모델에 "확인 후 안내"로 응답한다 — 카카오 콘솔 설정 없이 완료 가능한 범위 전부.

**Architecture:** 수신자를 `alarm_recipients` 테이블 행으로 관리하고 `admin_notify.notify_admins()`가 활성 행을 순회하며 발송한다. 수신자별 발송 실패는 개별 격리해 다른 수신자와 고객 응답에 영향을 주지 않는다. 해외 필터 키워드는 `price_filter_keywords` 테이블에 두고 5분 TTL 캐시로 읽는다. 고객 응답은 `get_inventory_status()` 한 곳에서 있음/없음/확인불가 3분기로 갈린다.

**Tech Stack:** FastAPI, async SQLAlchemy 2.0, asyncmy(MariaDB), httpx, 카카오 메시지 API(`memo/default/send`), 네이버쇼핑 검색 API

**설계 문서:** `docs/superpowers/specs/2026-07-29-kakao-multi-recipient-design.md`

## Global Constraints

- **테스트 프레임워크 없음.** pytest를 도입하지 말 것. 검증은 저장소 루트의 `test_*.py` standalone 스크립트(`asyncio.run(main())`가 서비스 함수를 직접 호출) 패턴을 따른다 — CLAUDE.md 명시 사항.
- **Windows 콘솔 인코딩.** 검증 스크립트 최상단에 `sys.stdout.reconfigure(encoding="utf-8")`를 넣는다. 없으면 한글/이모지 `print()`에서 크래시한다.
- **관리자 알림 실패가 고객 응답을 절대 막지 않는다.** 발송 경로의 모든 예외를 삼키고 로깅만 한다 (2026-07-17 코드리뷰 H9).
- **고객 응답에 수량/원가/마진/타사 단가/쇼핑몰명을 넣지 않는다.**
- **DB는 프로덕션 MariaDB.** `DATABASE_URL`이 `mysql+asyncmy`를 가리킨다. 스키마를 바꾸는 작업 전에는 `backups/`에 백업을 남긴다.
- **사용자 대면 문자열은 한국어.** 기존 이모지 프리픽스(`✅ 📦 📋 ⚙️ 🔩 🛒 📞 ☎️ ⚠️ ※`) 관례를 따른다.
- **시각 표기는 KST 고정.** 서버(Render)가 UTC라 `datetime.now()`를 쓰면 관리자에게 9시간 어긋난 시각이 간다. `timezone(timedelta(hours=9))`를 명시한다.

---

### Task 1: 테이블 2개 추가 + 기존 카카오 토큰 이관 + 키워드 시드

**Files:**
- Modify: `app/db/models.py` (파일 끝, `KakaoToken` 뒤)
- Create: `scripts/migrate_alarm_recipients.py`
- Create: `test_alarm_recipients.py` (저장소 루트)

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces:
  - `AlarmRecipient` — 컬럼: `id:int`, `name:str`, `channel:str`, `channel_token:str`, `access_token:str|None`, `token_expires_in:int|None`, `token_obtained_at:datetime|None`, `is_active:bool`, `created_at:datetime`
  - `PriceFilterKeyword` — 컬럼: `id:int`, `keyword:str`, `is_active:bool`, `note:str|None`

- [ ] **Step 1: 검증 스크립트를 먼저 작성한다 (실패하는 상태)**

Create `test_alarm_recipients.py`:

```python
"""alarm_recipients / price_filter_keywords 테이블과 초기 데이터 검증."""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from app.db.database import async_session
from app.db.models import AlarmRecipient, PriceFilterKeyword

EXPECTED_KEYWORDS = {"해외", "구매대행", "해외배송", "직구"}


async def main():
    ok = True
    async with async_session() as db:
        recipients = (await db.execute(select(AlarmRecipient))).scalars().all()
        print(f"수신자 {len(recipients)}명")
        for r in recipients:
            has_token = bool(r.channel_token)
            print(f"  #{r.id} {r.name} channel={r.channel} active={r.is_active} "
                  f"refresh_token={'있음' if has_token else '없음'}")

        active_kakao = [r for r in recipients if r.is_active and r.channel == "kakao"]
        if len(active_kakao) >= 1 and all(r.channel_token for r in active_kakao):
            print("[PASS] 활성 카카오 수신자 1명 이상, 전원 refresh_token 보유")
        else:
            print("[FAIL] 활성 카카오 수신자가 없거나 토큰이 비어 있음")
            ok = False

        keywords = (await db.execute(select(PriceFilterKeyword))).scalars().all()
        found = {k.keyword for k in keywords if k.is_active}
        print(f"\n활성 필터 키워드: {sorted(found)}")
        if EXPECTED_KEYWORDS <= found:
            print("[PASS] 해외 계열 키워드 4종 모두 존재")
        else:
            print(f"[FAIL] 누락: {sorted(EXPECTED_KEYWORDS - found)}")
            ok = False

    print("\n결과:", "통과" if ok else "실패")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 실행해서 실패를 확인한다**

Run: `python test_alarm_recipients.py`
Expected: `ImportError: cannot import name 'AlarmRecipient' from 'app.db.models'`

- [ ] **Step 3: 모델 2개를 추가한다**

`app/db/models.py` 끝의 `KakaoToken` 클래스 **뒤에** 추가:

```python
# ─── 9. 알림 수신자 (채널 일반형, 현재는 kakao만 사용) ───
class AlarmRecipient(Base):
    __tablename__ = "alarm_recipients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False)
    channel = Column(String(20), nullable=False, default="kakao")
    channel_token = Column(Text, nullable=False)   # 카카오: refresh_token / 훗날 slack: webhook URL
    # 아래 3개는 카카오 채널만 쓰는 단기 캐시 (다른 채널에서는 NULL)
    access_token = Column(Text)
    token_expires_in = Column(Integer)
    token_obtained_at = Column(DateTime)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


# ─── 10. 경쟁사 단가 제외 키워드 ───
class PriceFilterKeyword(Base):
    __tablename__ = "price_filter_keywords"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String(50), nullable=False, unique=True)
    is_active = Column(Boolean, default=True, nullable=False)
    note = Column(String(200))
```

- [ ] **Step 4: 마이그레이션 스크립트를 작성한다**

Create `scripts/migrate_alarm_recipients.py`:

```python
"""alarm_recipients / price_filter_keywords 생성 + 기존 카카오 토큰 이관 + 키워드 시드.

테이블 생성은 SQLAlchemy의 create_all(checkfirst=True)에 맡긴다. 프로덕션의 기존
테이블들은 pandas.to_sql로 만들어져 타입 드리프트가 있었지만(2026-07-28 H5,
2026-07-29 타입 드리프트 보정), create_all이 생성하는 DDL은 ORM 선언 그대로라
같은 문제가 생기지 않는다.

kakao_tokens의 단일 행(사장님 토큰)을 alarm_recipients 첫 행으로 옮긴다. 토큰
출처가 두 곳이면 갱신된 refresh_token이 한쪽에만 반영돼 다른 쪽이 조용히 죽는다.
kakao_tokens 테이블 자체는 롤백 대비로 남긴다.

멱등하다 — 여러 번 실행해도 중복 생성하지 않는다.

실행: python scripts/migrate_alarm_recipients.py
"""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select

from app.db.database import async_session, engine
from app.db.models import Base, AlarmRecipient, PriceFilterKeyword, KakaoToken

OWNER_NAME = "사장님"
SEED_KEYWORDS = [
    ("해외", "해외 판매/발송 표기"),
    ("구매대행", "구매대행 상품"),
    ("해외배송", "해외 직배송 상품"),
    ("직구", "해외 직구 상품"),
]


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[AlarmRecipient.__table__, PriceFilterKeyword.__table__],
            checkfirst=True,
        )
    print("[OK] 테이블 확인/생성 완료")

    async with async_session() as db:
        # ── 카카오 토큰 이관 ──
        existing = (await db.execute(
            select(AlarmRecipient).where(AlarmRecipient.name == OWNER_NAME)
        )).scalars().first()

        if existing:
            print(f"[SKIP] 수신자 '{OWNER_NAME}' 이미 존재 (id={existing.id})")
        else:
            token = (await db.execute(
                select(KakaoToken).where(KakaoToken.id == 1)
            )).scalars().first()
            if not token:
                print("[FAIL] kakao_tokens에 토큰이 없습니다. 최초 인증부터 하세요.")
                return
            db.add(AlarmRecipient(
                name=OWNER_NAME,
                channel="kakao",
                channel_token=token.refresh_token,
                access_token=token.access_token,
                token_expires_in=token.expires_in,
                token_obtained_at=token.obtained_at,
                is_active=True,
            ))
            await db.commit()
            print(f"[OK] 수신자 '{OWNER_NAME}' 이관 완료")

        # ── 키워드 시드 ──
        added = 0
        for keyword, note in SEED_KEYWORDS:
            found = (await db.execute(
                select(PriceFilterKeyword).where(PriceFilterKeyword.keyword == keyword)
            )).scalars().first()
            if found:
                continue
            db.add(PriceFilterKeyword(keyword=keyword, is_active=True, note=note))
            added += 1
        if added:
            await db.commit()
        print(f"[OK] 키워드 시드 완료 (신규 {added}건)")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: 백업 후 마이그레이션을 실행한다**

먼저 백업 — 기존 `backups/full_backup_pre_typefix_*.json`을 만든 것과 같은 방식으로 9개 테이블을 덤프한다. 이미 스크립트가 없으면 `SELECT *`를 돌려 JSON으로 저장하는 20줄짜리를 임시로 작성해도 된다.

Run: `python scripts/migrate_alarm_recipients.py`
Expected:
```
[OK] 테이블 확인/생성 완료
[OK] 수신자 '사장님' 이관 완료
[OK] 키워드 시드 완료 (신규 4건)
```

- [ ] **Step 6: 검증 스크립트가 통과하는지 확인한다**

Run: `python test_alarm_recipients.py`
Expected: `[PASS]` 2개, 마지막 줄 `결과: 통과`

- [ ] **Step 7: 생성된 DDL 타입을 눈으로 확인한다**

Run: `python -c` 금지 — 확인용 스크립트를 파일로 작성해 실행한다. `information_schema.COLUMNS`에서 두 테이블의 `COLUMN_TYPE`을 조회해 `text`가 아닌 실제 타입(`varchar(50)`, `tinyint(1)`, `datetime` 등)으로 만들어졌는지 본다.
Expected: `alarm_recipients.name`이 `varchar(50)`, `is_active`가 `tinyint(1)`, `created_at`이 `datetime` + `default=current_timestamp()`

- [ ] **Step 8: 커밋**

```bash
git add app/db/models.py scripts/migrate_alarm_recipients.py test_alarm_recipients.py
git commit -m "feat: 알림 수신자/가격필터 키워드 테이블 추가 및 기존 카카오 토큰 이관"
```

---

### Task 2: 경쟁사 단가 해외 필터 + 메시지 포맷(조회 시각)

**Files:**
- Modify: `app/services/admin_notify.py:144-191` (`_get_competitor_prices`, `_build_message`)
- Create: `test_price_filter.py` (저장소 루트)

**Interfaces:**
- Consumes: Task 1의 `PriceFilterKeyword`
- Produces:
  - `_load_filter_keywords(db: AsyncSession) -> list[str]`
  - `_get_competitor_prices(model_name: str, keywords: list[str], limit: int = 3) -> tuple[list[dict], int]` — `(경쟁사목록, 제외건수)`
  - `_build_message(model_name: str, stock_qty: int, stock_state: str, our_price: int | None, competitors: list[dict], excluded_count: int) -> str`

- [ ] **Step 1: 검증 스크립트를 먼저 작성한다**

Create `test_price_filter.py`:

```python
"""해외 필터와 메시지 포맷 검증 — 카카오 발송 없이 조립까지만 확인."""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.db.database import async_session
from app.services.admin_notify import (
    _load_filter_keywords, _get_competitor_prices, _build_message,
)

FAKE_ITEMS = [
    {"title": "해외 미쓰비시 MR-J4-70A 서보앰프", "mall": "직구몰", "price": 90000},
    {"title": "미쓰비시 MR-J4-70A 서보앰프", "mall": "국내스토어", "price": 410000},
    {"title": "MR-J4-70A 구매대행", "mall": "무역상사", "price": 88000},
]


def _apply(keywords, items):
    """_get_competitor_prices의 필터 판정과 같은 규칙을 검증용으로 재현."""
    kept, excluded = [], 0
    for it in items:
        if any(kw in f"{it['title']} {it['mall']}" for kw in keywords):
            excluded += 1
            continue
        kept.append(it)
    return kept, excluded


async def main():
    ok = True
    async with async_session() as db:
        keywords = await _load_filter_keywords(db)
        print(f"활성 키워드: {keywords}")

        kept, excluded = _apply(keywords, FAKE_ITEMS)
        print(f"가짜 데이터 3건 → 유지 {len(kept)}건 / 제외 {excluded}건")
        if len(kept) == 1 and excluded == 2:
            print("[PASS] 해외/구매대행 2건 제외, 국내 1건 유지")
        else:
            print("[FAIL] 필터 판정이 기대와 다름")
            ok = False

        # 일부 제외 메시지
        msg = _build_message(
            "MR-J4-70A", 2, "low_stock", 420000,
            [{"title": "미쓰비시 MR-J4-70A", "mall": "국내스토어", "price": 410000}],
            excluded_count=2,
        )
        print("\n── 일부 제외 ──")
        print(msg)
        if "※ 해외 표기 상품 2건은 비교 대상에서 제외됨" in msg and "조회 시각:" in msg:
            print("[PASS] 제외 표기 + 조회 시각 포함")
        else:
            print("[FAIL] 제외 표기 또는 조회 시각 누락")
            ok = False

        # 전부 제외 메시지
        msg_all = _build_message("MR-J4-70A", 2, "low_stock", 420000, [], excluded_count=3)
        print("\n── 전부 제외 ──")
        print(msg_all)
        if "경쟁사 단가: 해외 표기 상품으로 제외됨" in msg_all:
            print("[PASS] 전부 제외 문구")
        else:
            print("[FAIL] 전부 제외 문구 없음")
            ok = False

        # 검색 결과 자체가 없음
        msg_none = _build_message("존재하지않는모델", 0, "out_of_stock", None, [], excluded_count=0)
        if "타사 가격 검색 결과 없음" in msg_none:
            print("[PASS] 검색 결과 없음 문구 유지")
        else:
            print("[FAIL] 검색 결과 없음 문구가 바뀜")
            ok = False

        # 실제 API 1회 호출 (네트워크 확인용, 실패해도 위 판정과 무관)
        try:
            real, real_excluded = await _get_competitor_prices("MR-J4-70A", keywords)
            print(f"\n실제 네이버쇼핑 조회: 유지 {len(real)}건 / 제외 {real_excluded}건")
            for c in real:
                print(f"  · [{c['mall']}] {c['price']:,}원 - {c['title'][:40]}")
        except Exception as e:
            print(f"\n(실제 API 조회 실패 — 판정에는 영향 없음: {e})")

    print("\n결과:", "통과" if ok else "실패")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 실행해서 실패를 확인한다**

Run: `python test_price_filter.py`
Expected: `ImportError: cannot import name '_load_filter_keywords'`

- [ ] **Step 3: 키워드 로더를 추가한다**

`app/services/admin_notify.py`의 import에 추가:

```python
from datetime import datetime, timedelta, timezone
from app.db.models import (
    Product, StockAlert, PriceHistory, AlertChannel, KakaoToken,
    AlarmRecipient, PriceFilterKeyword,
)
```

`MY_MALL_KEYWORDS` 아래에 추가:

```python
KST = timezone(timedelta(hours=9))

# 필터 키워드 캐시 — 알림 1건마다 DB를 왕복할 이유가 없다.
_FILTER_CACHE_TTL = 300
_filter_cache: tuple[float, list[str]] | None = None

# DB 조회 자체가 실패했을 때만 쓰는 폴백. 관리자가 키워드를 전부 비활성화한
# 경우(빈 목록)는 "필터하지 말라"는 의도이므로 폴백하지 않는다.
_FALLBACK_FILTER_KEYWORDS = ["해외", "구매대행", "해외배송", "직구"]


async def _load_filter_keywords(db: AsyncSession) -> list[str]:
    """활성 제외 키워드 목록 (5분 캐시)."""
    global _filter_cache
    now = time.time()
    if _filter_cache and (now - _filter_cache[0]) < _FILTER_CACHE_TTL:
        return _filter_cache[1]

    try:
        rows = (await db.execute(
            select(PriceFilterKeyword.keyword).where(PriceFilterKeyword.is_active.is_(True))
        )).scalars().all()
        keywords = [k for k in rows if k]
    except Exception as e:
        logger.warning(f"[카카오알림] 필터 키워드 조회 실패, 기본값 사용: {e}")
        keywords = list(_FALLBACK_FILTER_KEYWORDS)

    _filter_cache = (now, keywords)
    return keywords
```

- [ ] **Step 4: `_get_competitor_prices`에 필터를 적용한다**

기존 시그니처를 바꾼다 (`app/services/admin_notify.py:144`):

```python
async def _get_competitor_prices(
    model_name: str, keywords: list[str], limit: int = 3
) -> tuple[list[dict], int]:
    """(경쟁사 목록, 제외 건수). 자사몰 제외는 제외 건수에 세지 않는다 —
    관리자가 알아야 할 것은 해외 상품이 몇 건 빠졌는지다."""
    async with httpx.AsyncClient(trust_env=False, timeout=10.0) as client:
        resp = await client.get(
            NAVER_SHOP_URL,
            params={"query": model_name, "display": 20, "sort": "asc"},
            headers={
                "X-Naver-Client-Id": settings.NAVER_SHOPPING_CLIENT_ID or settings.NAVER_CLIENT_ID,
                "X-Naver-Client-Secret": settings.NAVER_SHOPPING_CLIENT_SECRET or settings.NAVER_CLIENT_SECRET,
            },
        )
    if not resp.is_success:
        logger.warning(f"[카카오알림] 네이버쇼핑 검색 실패: {resp.text}")
        return [], 0

    items = resp.json().get("items", [])
    competitors = []
    excluded = 0
    for item in items:
        mall = item["mallName"]
        if any(kw in mall for kw in MY_MALL_KEYWORDS):
            continue
        title = item["title"].replace("<b>", "").replace("</b>", "")
        if any(kw in f"{title} {mall}" for kw in keywords):
            excluded += 1
            continue
        competitors.append({"title": title, "mall": mall, "price": int(item["lprice"])})

    return sorted(competitors, key=lambda x: x["price"])[:limit], excluded
```

- [ ] **Step 5: `_build_message`에 제외 표기와 조회 시각을 넣는다**

`app/services/admin_notify.py:172`의 함수를 통째로 교체:

```python
def _build_message(
    model_name: str,
    stock_qty: int,
    stock_state: str,
    our_price: int | None,
    competitors: list[dict],
    excluded_count: int = 0,
) -> str:
    state_label = {
        "out_of_stock": "품절", "low_stock": "재고 부족", "in_stock": "재고 있음",
    }.get(stock_state, stock_state)
    lines = [
        "📦 재고 알림",
        f"모델: {model_name}",
        "─────────────",
        f"상태: {state_label} ({stock_qty}개)",
    ]
    if our_price:
        lines.append(f"판매단가: {our_price:,}원")
    lines.append("─────────────")

    if competitors:
        lines.append("타사 가격 (참고용, 상품 일치 여부는 직접 확인 필요):")
        for c in competitors:
            lines.append(f"· [{c['mall']}] {c['price']:,}원 - {c['title'][:30]}")
        if excluded_count:
            lines.append(f"※ 해외 표기 상품 {excluded_count}건은 비교 대상에서 제외됨")
    elif excluded_count:
        lines.append(f"경쟁사 단가: 해외 표기 상품으로 제외됨 ({excluded_count}건)")
    else:
        lines.append("타사 가격 검색 결과 없음")

    lines.append("─────────────")
    # 서버(Render)가 UTC라 datetime.now()를 쓰면 관리자에게 9시간 어긋난 시각이 간다.
    lines.append(f"조회 시각: {datetime.now(KST):%Y-%m-%d %H:%M}")
    return "\n".join(lines)
```

- [ ] **Step 6: `notify_admin_kakao` 내부의 호출부를 맞춘다**

`app/services/admin_notify.py:215-221`을 수정 — Task 3에서 이 함수 전체를 다시 쓰지만, 지금 단계에서도 임포트 에러 없이 돌아가야 한다:

```python
    keywords = await _load_filter_keywords(db)
    try:
        competitors, excluded_count = await _get_competitor_prices(model_name, keywords)
    except Exception as e:
        logger.warning(f"[카카오알림] 타사가격 조회 실패: {e}")
        competitors, excluded_count = [], 0

    message = _build_message(model_name, stock_qty, stock_state, our_price, competitors, excluded_count)
```

- [ ] **Step 7: 검증 스크립트가 통과하는지 확인한다**

Run: `python test_price_filter.py`
Expected: `[PASS]` 4개, 마지막 줄 `결과: 통과`

- [ ] **Step 8: 커밋**

```bash
git add app/services/admin_notify.py test_price_filter.py
git commit -m "feat: 경쟁사 단가에서 해외 표기 상품 제외 + 알림에 조회 시각 추가"
```

---

### Task 3: 다중 수신자 발송 + 수신자별 실패 격리

**Files:**
- Modify: `app/services/admin_notify.py:41-139` (토큰 헬퍼, 헬스 상태), `196-262` (`notify_admin_kakao` → `notify_admins`)
- Modify: `app/services/inventory.py:9` (import), `282-283`, `301`
- Modify: `app/api/admin.py:96-100` (docstring)
- Create: `test_multi_recipient_notify.py` (저장소 루트)

**Interfaces:**
- Consumes: Task 1의 `AlarmRecipient`, Task 2의 `_load_filter_keywords`/`_get_competitor_prices`/`_build_message`
- Produces:
  - `notify_admins(db: AsyncSession, product: Product | None, model_name: str, stock_qty: int, stock_state: str, force: bool = False) -> dict` — 반환 `{"sent": int, "total": int, "skipped": str | None}`
  - `get_kakao_notify_health() -> dict` — 반환 `{"status": "ok" | "failing", "recipients": [{"id": int, "name": str, "reason": str, "since": str}]}`

- [ ] **Step 1: 검증 스크립트를 먼저 작성한다**

Create `test_multi_recipient_notify.py`:

```python
"""다중 수신자 발송 + 실패 격리 검증.

1단계: 발송 없이 수신자 목록/메시지 조립까지 확인 (dry)
2단계: 실제 발송 1회
3단계: 가짜 수신자(잘못된 토큰)를 끼워 넣어 다른 수신자와 고객 응답이 온전한지 확인
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from app.db.database import async_session
from app.db.models import AlarmRecipient, Product
from app.services import admin_notify
from app.services.admin_notify import notify_admins, get_kakao_notify_health
from app.services.inventory import get_inventory_status

MODEL = "MR-J4-70A"


async def main():
    ok = True

    # ── 1) 수신자 목록 + 메시지 조립 (발송 없이) ──
    sent_texts = []
    orig_send = admin_notify._send_kakao_text

    async def spy_no_send(db, recipient, text):
        sent_texts.append((recipient.name, text))
        return True

    admin_notify._send_kakao_text = spy_no_send

    async with async_session() as db:
        recipients = (await db.execute(
            select(AlarmRecipient).where(
                AlarmRecipient.is_active.is_(True), AlarmRecipient.channel == "kakao"
            )
        )).scalars().all()
        print(f"활성 카카오 수신자 {len(recipients)}명: {[r.name for r in recipients]}")

        product = (await db.execute(
            select(Product).where(Product.model_name == MODEL)
        )).scalars().first()

        result = await notify_admins(db, product, MODEL, 2, "low_stock", force=True)
        print(f"발송 결과(모의): {result}")

    if len(sent_texts) == len(recipients) and len(recipients) >= 1:
        print(f"[PASS] 수신자 {len(recipients)}명 전원에게 조립됨")
    else:
        print(f"[FAIL] 조립 {len(sent_texts)}건 vs 수신자 {len(recipients)}명")
        ok = False

    if sent_texts:
        print("\n── 조립된 메시지 ──")
        print(sent_texts[0][1])

    admin_notify._send_kakao_text = orig_send

    # ── 2) 실제 발송 1회 ──
    async with async_session() as db:
        product = (await db.execute(
            select(Product).where(Product.model_name == MODEL)
        )).scalars().first()
        real = await notify_admins(db, product, MODEL, 2, "low_stock", force=True)
        print(f"\n실제 발송: {real}")
    if real["sent"] >= 1:
        print("[PASS] 실제 카카오 발송 성공")
    else:
        print("[FAIL] 실제 발송 0건 — 카톡 확인 필요")
        ok = False

    # ── 3) 실패 격리: 토큰이 깨진 가짜 수신자를 끼워 넣는다 ──
    async with async_session() as db:
        db.add(AlarmRecipient(
            name="__테스트_깨진토큰__",
            channel="kakao",
            channel_token="invalid-refresh-token",
            is_active=True,
        ))
        await db.commit()

    try:
        async with async_session() as db:
            product = (await db.execute(
                select(Product).where(Product.model_name == MODEL)
            )).scalars().first()
            mixed = await notify_admins(db, product, MODEL, 2, "low_stock", force=True)
            print(f"\n깨진 수신자 포함 발송: {mixed}")

        if mixed["sent"] >= 1 and mixed["sent"] < mixed["total"]:
            print("[PASS] 한 명 실패해도 나머지는 발송됨")
        else:
            print("[FAIL] 실패 격리가 동작하지 않음")
            ok = False

        health = get_kakao_notify_health()
        print(f"헬스 상태: {health}")
        if health["status"] == "failing" and any(
            r["name"] == "__테스트_깨진토큰__" for r in health.get("recipients", [])
        ):
            print("[PASS] 실패한 수신자를 이름으로 식별 가능")
        else:
            print("[FAIL] 헬스 상태에 실패 수신자가 안 잡힘")
            ok = False

        # 고객 응답이 온전한지
        async with async_session() as db:
            reply = await get_inventory_status(MODEL, db)
        if "재고" in reply and "오류" not in reply:
            print("[PASS] 발송 실패가 있어도 고객 응답 정상")
        else:
            print(f"[FAIL] 고객 응답이 손상됨: {reply[:80]}")
            ok = False

    finally:
        # 가짜 수신자 정리
        async with async_session() as db:
            fake = (await db.execute(
                select(AlarmRecipient).where(AlarmRecipient.name == "__테스트_깨진토큰__")
            )).scalars().first()
            if fake:
                await db.delete(fake)
                await db.commit()
                print("\n가짜 수신자 정리 완료")

    print("\n결과:", "통과" if ok else "실패")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 실행해서 실패를 확인한다**

Run: `python test_multi_recipient_notify.py`
Expected: `ImportError: cannot import name 'notify_admins'`

- [ ] **Step 3: 토큰 헬퍼를 수신자 단위로 바꾼다**

`app/services/admin_notify.py:38-45`의 `_kakao_last_failure`와 `get_kakao_notify_health()`를 교체:

```python
# 수신자별 토큰 실패 상태(서버 재시작 시 초기화). 전역 단일 값이면 누구의 토큰이
# 끊겼는지 알 수 없어 재인증 대상을 특정할 수 없다.
_kakao_failures: dict[int, dict] = {}


def get_kakao_notify_health() -> dict:
    """카카오 알림 발송 가능 상태. /api/admin/kakao-status에서 노출."""
    if not _kakao_failures:
        return {"status": "ok", "recipients": []}
    return {
        "status": "failing",
        "recipients": [
            {"id": rid, **info} for rid, info in sorted(_kakao_failures.items())
        ],
    }
```

`_load_kakao_token`/`_save_kakao_token`/`_get_valid_kakao_access_token` 세 함수(`app/services/admin_notify.py:50-117`)를 아래로 교체:

```python
async def _get_valid_access_token(db: AsyncSession, recipient: AlarmRecipient) -> str | None:
    """수신자 1명의 유효한 access_token. 만료되었으면 refresh_token으로 갱신한다."""
    if recipient.access_token and recipient.token_obtained_at and recipient.token_expires_in:
        elapsed = (datetime.utcnow() - recipient.token_obtained_at).total_seconds()
        if elapsed < recipient.token_expires_in - 60:
            return recipient.access_token

    async with httpx.AsyncClient(trust_env=False, timeout=10.0) as client:
        resp = await client.post(
            f"{KAUTH_BASE}/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": settings.KAKAO_REST_API_KEY,
                "client_secret": settings.KAKAO_CLIENT_SECRET,
                "refresh_token": recipient.channel_token,
            },
        )

    if not resp.is_success:
        logger.error(f"[카카오알림] '{recipient.name}' 토큰 갱신 실패: {resp.text}")
        # 로그만 남기고 넘어가면 이후 모든 알림이 조용히 실패한다(코드리뷰 H10).
        _kakao_failures.setdefault(recipient.id, {
            "name": recipient.name,
            "reason": f"토큰 갱신 실패 (재인증 필요): {resp.text[:200]}",
            "since": datetime.utcnow().isoformat(),
        })
        return None

    token = resp.json()
    recipient.access_token = token["access_token"]
    recipient.token_expires_in = token["expires_in"]
    recipient.token_obtained_at = datetime.utcnow()
    # refresh_token은 응답에 없을 수도 있다 (기존 값 유지)
    if token.get("refresh_token"):
        recipient.channel_token = token["refresh_token"]
    await db.commit()

    _kakao_failures.pop(recipient.id, None)
    return recipient.access_token
```

- [ ] **Step 4: 발송 함수를 수신자 인자로 바꾼다**

`app/services/admin_notify.py:120-139`의 `_send_kakao_text`를 교체:

```python
async def _send_kakao_text(db: AsyncSession, recipient: AlarmRecipient, text: str) -> bool:
    access_token = await _get_valid_access_token(db, recipient)
    if not access_token:
        return False

    template_object = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": "https://smartstore.naver.com/hdauto22"},
    }
    async with httpx.AsyncClient(trust_env=False, timeout=10.0) as client:
        resp = await client.post(
            f"{KAPI_BASE}/v2/api/talk/memo/default/send",
            headers={"Authorization": f"Bearer {access_token}"},
            data={"template_object": json.dumps(template_object, ensure_ascii=False)},
        )
    if not resp.is_success:
        logger.error(f"[카카오알림] '{recipient.name}' 발송 실패: {resp.text}")
        _kakao_failures.setdefault(recipient.id, {
            "name": recipient.name,
            "reason": f"발송 실패: {resp.text[:200]}",
            "since": datetime.utcnow().isoformat(),
        })
        return False

    _kakao_failures.pop(recipient.id, None)
    return True
```

- [ ] **Step 5: `notify_admin_kakao`를 `notify_admins`로 교체한다**

`app/services/admin_notify.py:196-262`를 통째로 교체:

```python
async def notify_admins(
    db: AsyncSession,
    product: Product | None,
    model_name: str,
    stock_qty: int,
    stock_state: str,  # "out_of_stock" | "low_stock" | "in_stock"
    force: bool = False,
) -> dict:
    """활성 수신자 전원에게 재고 알림 + DB 기록(StockAlert, PriceHistory).

    같은 모델명은 _DEBOUNCE_SECONDS 이내 재알림을 스킵한다. 디바운스는 수신자별이
    아니라 모델별로 한 번 판정한다 — 같은 알림이 사람마다 다른 시각에 나가면
    대조가 어렵다.

    Returns: {"sent": 발송성공수, "total": 대상수신자수, "skipped": 사유 | None}
    """
    now = time.time()
    if not force and (now - _last_notified.get(model_name, 0)) < _DEBOUNCE_SECONDS:
        logger.info(f"[카카오알림] '{model_name}' 디바운스 스킵")
        return {"sent": 0, "total": 0, "skipped": "debounce"}

    recipients = (await db.execute(
        select(AlarmRecipient).where(
            AlarmRecipient.is_active.is_(True),
            AlarmRecipient.channel == "kakao",
        ).order_by(AlarmRecipient.id)
    )).scalars().all()

    if not recipients:
        logger.warning("[카카오알림] 활성 수신자가 없습니다 — 알림을 보내지 않습니다.")
        return {"sent": 0, "total": 0, "skipped": "no_recipients"}

    our_price = product.our_price if product else None

    keywords = await _load_filter_keywords(db)
    try:
        competitors, excluded_count = await _get_competitor_prices(model_name, keywords)
    except Exception as e:
        logger.warning(f"[카카오알림] 타사가격 조회 실패: {e}")
        competitors, excluded_count = [], 0

    message = _build_message(
        model_name, stock_qty, stock_state, our_price, competitors, excluded_count
    )

    sent = 0
    for recipient in recipients:
        try:
            if await _send_kakao_text(db, recipient, message):
                sent += 1
        except Exception as e:
            # 한 명의 네트워크/DNS 실패가 나머지 수신자 발송을 막아선 안 된다.
            logger.error(f"[카카오알림] '{recipient.name}' 발송 중 예외: {e}")
            _kakao_failures.setdefault(recipient.id, {
                "name": recipient.name,
                "reason": f"발송 중 예외: {e}",
                "since": datetime.utcnow().isoformat(),
            })

    # DB 기록 (product가 카탈로그에 있을 때만 — 없으면 FK 위반이라 스킵)
    if product:
        try:
            db.add(StockAlert(
                product_id=product.id,
                alert_type=stock_state,
                channel=AlertChannel.KAKAO,
                resolved=False,
            ))

            if competitors and our_price:
                prices = [c["price"] for c in competitors]
                competitor_min = min(prices)
                diff_percent = round((our_price - competitor_min) / our_price * 100, 1)
                db.add(PriceHistory(
                    product_id=product.id,
                    our_price=our_price,
                    competitor_min=competitor_min,
                    competitor_avg=round(sum(prices) / len(prices)),
                    competitor_max=max(prices),
                    competitor_count=len(prices),
                    diff_percent=diff_percent,
                    needs_adjustment=diff_percent > settings.PRICE_DIFF_THRESHOLD,
                ))
            await db.commit()
        except Exception as e:
            logger.warning(f"[카카오알림] DB 기록 실패: {e}")
            await db.rollback()

    if sent:
        _last_notified[model_name] = now
    return {"sent": sent, "total": len(recipients), "skipped": None}
```

- [ ] **Step 6: `KakaoToken` 참조를 제거한다**

`app/services/admin_notify.py`의 import에서 `KakaoToken`을 뺀다. 토큰 출처가 두 곳이면 갱신된 refresh_token이 한쪽에만 반영돼 다른 쪽이 조용히 죽는다. 테이블 자체는 롤백 대비로 남기지만 코드는 더 이상 읽지 않는다.

- [ ] **Step 7: 호출부를 고친다**

`app/services/inventory.py:9`:

```python
from app.services.admin_notify import notify_admins
```

`app/services/inventory.py:283` (out_of_stock 분기 안):

```python
        await notify_admins(db, product, product_name, stock["quantity"], "out_of_stock")
```

`app/services/inventory.py:301` (low_stock 분기 안):

```python
        await notify_admins(db, product, product_name, stock["quantity"], "low_stock")
```

`app/api/admin.py:99`의 docstring을 갱신:

```python
    """refresh_token 만료/무효화로 알림이 조용히 실패 중인지 확인 (코드리뷰 H10).
    status가 "failing"이면 recipients 목록의 수신자가 재인증 대상이다."""
```

- [ ] **Step 8: 검증 스크립트가 통과하는지 확인한다**

Run: `python test_multi_recipient_notify.py`
Expected: `[PASS]` 5개, 마지막 줄 `결과: 통과`, 그리고 카카오톡 "나와의 채팅"에 실제 메시지 도착

- [ ] **Step 9: 커밋**

```bash
git add app/services/admin_notify.py app/services/inventory.py app/api/admin.py test_multi_recipient_notify.py
git commit -m "feat: 재고 알림을 활성 수신자 전원에게 발송하고 수신자별 실패를 격리"
```

---

### Task 4: 고객 응답 3분기 + 알림 트리거 확대

**Files:**
- Modify: `app/services/inventory.py:195-211` (재고 상태 판정), `278-294` (분기), `296-318` (재고 있음 분기)
- Create: `test_stock_reply_rules.py` (저장소 루트)

**Interfaces:**
- Consumes: Task 3의 `notify_admins`
- Produces: `get_stock_state`/`get_inventory_status`가 `"unknown"` 상태를 추가로 다룬다 (기존 `"in_stock" | "low_stock" | "out_of_stock"`에 더해)

- [ ] **Step 1: 검증 스크립트를 먼저 작성한다**

Create `test_stock_reply_rules.py`:

```python
"""응대 규칙 검증 — 3분기 응답, 내부정보 미노출, 알림 트리거 범위."""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.db.database import async_session
from app.services import admin_notify
from app.services.inventory import get_inventory_status

# (모델명, 기대 상태) — 재고 상황은 바뀔 수 있으므로 문구 존재 여부로만 판정
UNKNOWN_MODEL = "존재하지않는모델XYZ999"
IN_STOCK_MODEL = "MR-J4-40A"

FORBIDDEN = ["원가", "마진", "타사 가격", "판매단가"]


async def main():
    ok = True
    calls = []
    orig = admin_notify.notify_admins

    async def spy(db, product, model_name, qty, state, force=False):
        calls.append((model_name, state))
        return {"sent": 0, "total": 0, "skipped": "test"}

    admin_notify.notify_admins = spy
    # inventory.py가 from-import로 잡아둔 참조까지 교체
    import app.services.inventory as inv_mod
    inv_mod.notify_admins = spy

    # ── 1) 미매칭 → "확인 후 안내", 알림 없음 ──
    calls.clear()
    async with async_session() as db:
        reply = await get_inventory_status(UNKNOWN_MODEL, db)
    print("── 미매칭 응답 ──")
    print(reply)
    if "확인 후 안내" in reply and "재고 없음" not in reply:
        print("[PASS] 미매칭은 재고를 단정하지 않고 확인 후 안내")
    else:
        print("[FAIL] 미매칭 응답이 규칙과 다름")
        ok = False
    if not calls:
        print("[PASS] 미매칭은 관리자 알림을 보내지 않음")
    else:
        print(f"[FAIL] 미매칭인데 알림 호출됨: {calls}")
        ok = False

    # ── 2) 재고 있음 → 알림 발생 ──
    calls.clear()
    async with async_session() as db:
        reply2 = await get_inventory_status(IN_STOCK_MODEL, db)
    print("\n── 재고 있음 응답 ──")
    print(reply2)
    if calls and calls[0][1] in ("in_stock", "low_stock"):
        print(f"[PASS] 재고 있어도 관리자 알림 호출됨 ({calls[0][1]})")
    else:
        print(f"[FAIL] 재고 있음인데 알림 호출 안 됨: {calls}")
        ok = False

    # ── 3) 내부 정보 미노출 ──
    leaked = [w for w in FORBIDDEN if w in reply or w in reply2]
    if not leaked:
        print("[PASS] 고객 응답에 내부 정보 없음")
    else:
        print(f"[FAIL] 내부 정보 노출: {leaked}")
        ok = False

    admin_notify.notify_admins = orig
    inv_mod.notify_admins = orig
    print("\n결과:", "통과" if ok else "실패")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 실행해서 실패를 확인한다**

Run: `python test_stock_reply_rules.py`
Expected: 미매칭 응답이 `📦 '존재하지않는모델XYZ999' 재고 없음`으로 나와 `[FAIL] 미매칭 응답이 규칙과 다름`, 재고 있음 알림도 `[FAIL]`

- [ ] **Step 3: 미매칭을 `unknown` 상태로 분리한다**

`app/services/inventory.py:195-211`을 교체:

```python
    if product:
        stock = await get_stock_state(product, db)
    else:
        # DB 카탈로그에 없는 모델 → 바로 out_of_stock 단정하지 않고
        # 네이버 실시간 검색으로 한 번 더 확인
        direct = await _check_naver_directly(model_name)
        if direct is None:
            # 스마트스토어에서도 상품 자체를 못 찾음 → 재고 유무를 단정할 수 없다.
            # "재고 없음"으로 단정하면 카탈로그에 없는 취급 모델의 주문을 놓친다.
            stock = {"quantity": 0, "source": "none", "state": "unknown", "min_threshold": 0}
        elif direct["quantity"] > 0:
            stock = {
                "quantity": direct["quantity"],
                "source": "naver",
                "state": "in_stock",
                "min_threshold": settings.DEFAULT_STOCK_THRESHOLD,
            }
            product_name = direct["matched_name"]
        else:
            # 스마트스토어에 상품은 있는데 재고가 0 → 진짜 품절
            stock = {"quantity": 0, "source": "naver", "state": "out_of_stock", "min_threshold": 0}
            product_name = direct["matched_name"]
```

- [ ] **Step 4: `unknown` 응답 분기를 추가한다**

`app/services/inventory.py`의 `_build_replacement_block()` 정의 **직후**, `# 4) 재고 없음` 주석 **앞에** 삽입:

```python
    # ────────────────────────────────────────────
    # 3-5) 재고 확인 불가 (DB·스마트스토어 모두 미매칭)
    # ────────────────────────────────────────────
    if stock["state"] == "unknown":
        # 재고 유무만 단정하지 않을 뿐, 대체품 안내 자체는 고객에게 유용하므로 유지한다.
        replacement_block = await _build_replacement_block()
        return (
            f"{desc_note}"
            f"🔎 '{model_name}'은(는) 정확한 재고 확인을 위해 확인 후 안내드리겠습니다.\n\n"
            f"{companion_note}"
            f"{replacement_block}\n\n"
            f"📞 현대자동화로 연락주시면 바로 확인해 드리겠습니다.\n"
            f"☎️ {COMPANY_PHONE}"
        )
```

- [ ] **Step 5: 재고 있음에도 알림을 보낸다**

`app/services/inventory.py:299-303`을 교체:

```python
    if stock["state"] == "low_stock":
        stock_label = "✅ 재고 있음 (소진 임박 — 서두르시는 걸 권장드립니다)"
    else:
        stock_label = "✅ 재고 있음"

    # 규칙 문서: 재고 조회 의도로 매칭에 성공한 경우 매번 알림. 재고가 충분한
    # 문의도 "어떤 상품을 고객이 찾고 있는가"라는 수요 신호로 쓴다. 발송량은
    # notify_admins의 모델별 1시간 디바운스가 억제한다.
    await notify_admins(db, product, product_name, stock["quantity"], stock["state"])
```

- [ ] **Step 6: 검증 스크립트가 통과하는지 확인한다**

Run: `python test_stock_reply_rules.py`
Expected: `[PASS]` 4개, 마지막 줄 `결과: 통과`

- [ ] **Step 7: 기존 회귀 스크립트를 돌린다**

Run: `python test_inventory.py`
Expected: 예외 없이 완주. 미매칭 모델(`존재하지않는모델XYZ999`) 응답이 "재고 없음"에서 "확인 후 안내"로 바뀐 것을 눈으로 확인.

Run: `python test_price_filter.py`
Expected: `결과: 통과` (Task 2 회귀 없음)

- [ ] **Step 8: 커밋**

```bash
git add app/services/inventory.py test_stock_reply_rules.py
git commit -m "feat: 재고 확인 불가 응답 분기 추가 및 재고 있음에도 관리자 알림"
```

---

### Task 5: 통합 검증 및 1단계 마무리

**Files:**
- Modify: `CLAUDE.md` (재고 알림 구조 설명 갱신)
- Modify: `docs/superpowers/specs/2026-07-29-kakao-multi-recipient-design.md` (create_all 관련 수정)

**Interfaces:**
- Consumes: Task 1~4 전부
- Produces: 없음 (문서화 태스크)

- [ ] **Step 1: 전체 흐름을 실제 발송까지 한 번 돌린다**

Run 순서대로:
```
python test_alarm_recipients.py
python test_price_filter.py
python test_multi_recipient_notify.py
python test_stock_reply_rules.py
python test_inventory.py
```
Expected: 앞의 4개가 모두 `결과: 통과`, `test_inventory.py`는 예외 없이 완주. 카카오톡에 실제 알림 도착 확인.

- [ ] **Step 2: 알림 기록이 남았는지 확인한다**

`stock_alerts`와 `price_history`의 최근 행을 조회하는 스크립트를 파일로 작성해 실행한다. `sent_at`/`checked_at`이 NULL이 아니어야 한다 (2026-07-29 타입 드리프트 보정으로 DEFAULT CURRENT_TIMESTAMP가 들어갔다).
Expected: 방금 발송한 알림의 `sent_at`에 시각이 기록됨

- [ ] **Step 3: CLAUDE.md의 알림 관련 서술을 갱신한다**

"Low/out-of-stock triggers a Kakao..." 문단을 아래 내용으로 고친다:
- 트리거가 `low_stock`/`out_of_stock`뿐 아니라 재고 조회 매칭 성공 시 전부라는 점
- 토큰이 `KakaoToken` 싱글턴이 아니라 `AlarmRecipient` 행 단위로 관리된다는 점
- 경쟁사 단가에 `price_filter_keywords` 기반 해외 필터가 적용된다는 점
- 수신자별 실패가 격리되며 `/api/admin/kakao-status`가 수신자별 상태를 반환한다는 점

- [ ] **Step 4: 스펙 문서의 사실관계를 바로잡는다**

설계 문서의 "신규 두 테이블은 마이그레이션 스크립트에서 명시적 `CREATE TABLE`로 만든다" 문장을 수정한다. `init_db()`가 시작 시 `Base.metadata.create_all`을 호출하므로 ORM 선언대로 DDL이 생성되며, `pandas.to_sql` 드리프트 문제는 최초 이관 테이블에만 해당한다. 마이그레이션 스크립트는 `create_all(checkfirst=True)` 호출과 데이터 시드만 담당한다.

- [ ] **Step 5: 커밋**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-07-29-kakao-multi-recipient-design.md
git commit -m "docs: 다중 수신자 알림 구조 반영 및 스펙의 테이블 생성 방식 정정"
```

---

## 2단계로 미룬 것

카카오 개발자콘솔 설정(배포 도메인 Redirect URI 등록, 앱 배포 상태 전환 또는 팀원 등록)과
두 번째 카카오 계정이 있어야 검증 가능한 것들이다.

- `GET /api/admin/recipients/connect` — 초대 링크 발급 (1회용 논스)
- `GET /api/admin/recipients/callback` — 동의 후 `alarm_recipients` upsert
- 두 번째 실제 수신자로 다중 발송 최종 확인

1단계가 끝나면 수신자 1명(사장님)으로 다중 발송 경로 전체가 검증된 상태가 된다. 2단계는
수신자를 늘리는 입구만 추가하는 작업이다.
