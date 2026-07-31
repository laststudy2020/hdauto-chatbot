"""
재고 부족/품절 시 관리자에게 카카오톡 "나에게 보내기"로 알림을 보내는 서비스.

기존 inventory.py의 _notify_admin(Slack)과 나란히 동작하도록 설계.
StockAlert / PriceHistory 테이블에 기록을 남기고, 카카오로 메시지를 발송한다.

사용처: app/services/inventory.py의 get_inventory_status()에서 호출.
"""

import json
import time
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import (
    Product, StockAlert, PriceHistory, AlertChannel,
    AlarmRecipient, PriceFilterKeyword,
)

logger = logging.getLogger(__name__)
settings = get_settings()

KAUTH_BASE = "https://kauth.kakao.com"
KAPI_BASE = "https://kapi.kakao.com"
NAVER_SHOP_URL = "https://openapi.naver.com/v1/search/shop.json"

MY_MALL_KEYWORDS = ["현대자동화", "hdauto"]

KST = timezone(timedelta(hours=9))

# 필터 키워드 캐시 — 알림 1건마다 DB를 왕복할 이유가 없다.
_FILTER_CACHE_TTL = 300
_filter_cache: tuple[float, list[str]] | None = None

# DB 조회 자체가 실패했을 때만 쓰는 폴백. 관리자가 키워드를 전부 비활성화한
# 경우(빈 목록)는 "필터하지 말라"는 의도이므로 폴백하지 않는다.
_FALLBACK_FILTER_KEYWORDS = ["해외", "구매대행", "해외배송", "직구"]

# 재고 상태별 디바운스(같은 모델 반복 알림 방지). 서버 재시작 시 초기화.
_DEBOUNCE_SECONDS = 60 * 60
_last_notified: dict[str, float] = {}

# 수신자별 토큰/발송 실패 상태(서버 재시작 시 초기화). refresh_token이 만료/무효화되면
# 로그만 남기고 이후 모든 알림이 계속 조용히 실패하던 것을, get_kakao_notify_health()로
# 조회 가능하게 노출해 관리자가 재인증 필요 여부를 확인할 수 있게 한다(코드리뷰 H10).
# 전역 단일 값이면 수신자가 여럿일 때 누구의 토큰이 끊겼는지 알 수 없어 재인증 대상을
# 특정할 수 없으므로 수신자 id별로 기록한다.
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


# ───────────────────── 카카오 토큰 (수신자별 DB 저장) ─────────────────────

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
        # refresh_token 만료/무효화 시 로그만 남기고 넘어가면 관리자가 알 방법이
        # 없어 이후 모든 재고 알림이 계속 조용히 실패한다(코드리뷰 H10).
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


# ───────────────────── 네이버쇼핑 타사 가격 ─────────────────────

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


# ───────────────────── 메시지 조립 ─────────────────────

def _build_message(
    model_name: str,
    stock_qty: int,
    stock_state: str,
    our_price: int | None,
    competitors: list[dict],
    excluded_count: int = 0,
) -> str:
    state_label = {"out_of_stock": "품절", "low_stock": "재고 부족", "in_stock": "재고 있음"}.get(stock_state, stock_state)
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


# ───────────────────── 외부 호출 함수 ─────────────────────

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
    previous = _last_notified.get(model_name)
    if not force and previous is not None and (now - previous) < _DEBOUNCE_SECONDS:
        logger.info(f"[카카오알림] '{model_name}' 디바운스 스킵")
        return {"sent": 0, "total": 0, "skipped": "debounce"}

    # 디바운스 슬롯을 발송 '전에' 선점한다. 위 검사와 실제 기록 사이에는 수신자 조회·
    # 타사가격 조회·카카오 발송·커밋까지 await가 여러 번 있어서, 인기 모델을 여러 고객이
    # 동시에 물어보면 두 요청이 모두 검사를 통과해 같은 알림이 중복 발송된다.
    # 발송이 한 건도 성공하지 못하면 아래 finally에서 이전 값으로 되돌려, 실패한 시도가
    # 한 시간 동안 재알림을 막지 않게 한다.
    _last_notified[model_name] = now
    sent = 0

    try:
        recipients = (await db.execute(
            select(AlarmRecipient).where(
                AlarmRecipient.is_active.is_(True),
                AlarmRecipient.channel == "kakao",
            ).order_by(AlarmRecipient.id)
        )).scalars().all()

        # 삭제/비활성화된 수신자의 실패 기록이 남으면 헬스 상태가 영구히 "failing"으로
        # 보여 진짜 장애를 놓친다. 발송 대상에서 빠진 수신자의 기록은 지운다.
        active_ids = {r.id for r in recipients}
        for stale_id in [rid for rid in _kakao_failures if rid not in active_ids]:
            _kakao_failures.pop(stale_id, None)

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
        for recipient in recipients:
            try:
                if await _send_kakao_text(db, recipient, message):
                    sent += 1
            except Exception as e:
                # 카카오 발송 실패(네트워크/DNS 등)가 여기서 안 잡히면 예외가 get_inventory_status()를
                # 거쳐 고객에게 갔어야 할 재고 응답 전체를 범용 오류 메시지로 대체해버린다(코드리뷰 H9).
                # 또한 한 명의 실패가 나머지 수신자 발송까지 막아선 안 된다.
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

        return {"sent": sent, "total": len(recipients), "skipped": None}
    finally:
        # 되돌리기는 우리가 찍은 값이 그대로 남아 있을 때만 한다. 그 사이 다른 호출이
        # (force=True로) 슬롯을 다시 선점했다면 그쪽 기록을 지워선 안 된다.
        if not sent and _last_notified.get(model_name) == now:
            if previous is None:
                _last_notified.pop(model_name, None)
            else:
                _last_notified[model_name] = previous