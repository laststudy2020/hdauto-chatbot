"""
재고 부족/품절 시 관리자에게 카카오톡 "나에게 보내기"로 알림을 보내는 서비스.

기존 inventory.py의 _notify_admin(Slack)과 나란히 동작하도록 설계.
StockAlert / PriceHistory 테이블에 기록을 남기고, 카카오로 메시지를 발송한다.

사용처: app/services/inventory.py의 get_inventory_status()에서 호출.
"""

import json
import time
import logging
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Product, StockAlert, PriceHistory, AlertChannel, KakaoToken

logger = logging.getLogger(__name__)
settings = get_settings()

KAUTH_BASE = "https://kauth.kakao.com"
KAPI_BASE = "https://kapi.kakao.com"
NAVER_SHOP_URL = "https://openapi.naver.com/v1/search/shop.json"

MY_MALL_KEYWORDS = ["현대자동화", "hdauto"]

# 재고 상태별 디바운스(같은 모델 반복 알림 방지). 서버 재시작 시 초기화.
_DEBOUNCE_SECONDS = 60 * 60
_last_notified: dict[str, float] = {}

# 카카오 토큰 갱신 실패 상태(서버 재시작 시 초기화). refresh_token이 만료/무효화되면
# 로그만 남기고 이후 모든 알림이 계속 조용히 실패하던 것을, get_kakao_notify_health()로
# 조회 가능하게 노출해 관리자가 재인증 필요 여부를 확인할 수 있게 한다(코드리뷰 H10).
_kakao_last_failure: dict | None = None


def get_kakao_notify_health() -> dict:
    """카카오 알림 발송 가능 상태. /api/admin/kakao-status에서 노출."""
    if _kakao_last_failure is None:
        return {"status": "ok"}
    return {"status": "failing", **_kakao_last_failure}


# ───────────────────── 카카오 토큰 (DB 저장) ─────────────────────

async def _load_kakao_token(db: AsyncSession) -> KakaoToken | None:
    result = await db.execute(select(KakaoToken).where(KakaoToken.id == 1))
    return result.scalars().first()


async def _save_kakao_token(db: AsyncSession, token: dict):
    existing = await _load_kakao_token(db)
    now = datetime.utcnow()
    if existing:
        existing.access_token = token["access_token"]
        existing.refresh_token = token.get("refresh_token", existing.refresh_token)
        existing.expires_in = token["expires_in"]
        existing.obtained_at = now
    else:
        db.add(KakaoToken(
            id=1,
            access_token=token["access_token"],
            refresh_token=token["refresh_token"],
            expires_in=token["expires_in"],
            obtained_at=now,
        ))
    await db.commit()


async def _get_valid_kakao_access_token(db: AsyncSession) -> str | None:
    global _kakao_last_failure

    row = await _load_kakao_token(db)
    if not row:
        logger.warning("[카카오알림] kakao_tokens 테이블에 토큰 없음 — 최초 인증 필요")
        if _kakao_last_failure is None:
            _kakao_last_failure = {
                "reason": "토큰 없음 — 최초 인증 필요",
                "since": datetime.utcnow().isoformat(),
            }
        return None

    elapsed = (datetime.utcnow() - row.obtained_at).total_seconds()
    if elapsed < row.expires_in - 60:
        return row.access_token

    async with httpx.AsyncClient(trust_env=False, timeout=10.0) as client:
        resp = await client.post(
            f"{KAUTH_BASE}/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": settings.KAKAO_REST_API_KEY,
                "client_secret": settings.KAKAO_CLIENT_SECRET,
                "refresh_token": row.refresh_token,
            },
        )
    if not resp.is_success:
        logger.error(f"[카카오알림] 토큰 갱신 실패: {resp.text}")
        # refresh_token 만료/무효화 시 로그만 남기고 넘어가면 관리자가 알 방법이
        # 없어 이후 모든 재고 알림이 계속 조용히 실패한다(코드리뷰 H10) — 실패
        # 상태를 기억해 get_kakao_notify_health()로 조회 가능하게 한다.
        if "since" not in (_kakao_last_failure or {}):
            _kakao_last_failure = {
                "reason": f"토큰 갱신 실패 (재인증 필요): {resp.text[:200]}",
                "since": datetime.utcnow().isoformat(),
            }
        return None

    new_token = resp.json()
    new_token.setdefault("refresh_token", row.refresh_token)
    await _save_kakao_token(db, new_token)
    _kakao_last_failure = None
    return new_token["access_token"]


async def _send_kakao_text(db: AsyncSession, text: str) -> bool:
    access_token = await _get_valid_kakao_access_token(db)
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
        logger.error(f"[카카오알림] 발송 실패: {resp.text}")
        return False
    return True


# ───────────────────── 네이버쇼핑 타사 가격 ─────────────────────

async def _get_competitor_prices(model_name: str, limit: int = 3) -> list[dict]:
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
        return []

    items = resp.json().get("items", [])
    competitors = []
    for item in items:
        mall = item["mallName"]
        if any(kw in mall for kw in MY_MALL_KEYWORDS):
            continue
        title = item["title"].replace("<b>", "").replace("</b>", "")
        competitors.append({"title": title, "mall": mall, "price": int(item["lprice"])})

    return sorted(competitors, key=lambda x: x["price"])[:limit]


# ───────────────────── 메시지 조립 ─────────────────────

def _build_message(model_name: str, stock_qty: int, stock_state: str, our_price: int | None, competitors: list[dict]) -> str:
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
    else:
        lines.append("타사 가격 검색 결과 없음")

    return "\n".join(lines)


# ───────────────────── 외부 호출 함수 ─────────────────────

async def notify_admin_kakao(
    db: AsyncSession,
    product: Product | None,
    model_name: str,
    stock_qty: int,
    stock_state: str,  # "out_of_stock" | "low_stock" | "in_stock"
    force: bool = False,
) -> bool:
    """
    재고 부족/품절 시 카카오 알림 + DB 기록(StockAlert, PriceHistory).
    같은 모델명은 _DEBOUNCE_SECONDS 이내 재알림 스킵.
    """
    now = time.time()
    if not force and (now - _last_notified.get(model_name, 0)) < _DEBOUNCE_SECONDS:
        logger.info(f"[카카오알림] '{model_name}' 디바운스 스킵")
        return False

    our_price = product.our_price if product else None

    try:
        competitors = await _get_competitor_prices(model_name)
    except Exception as e:
        logger.warning(f"[카카오알림] 타사가격 조회 실패: {e}")
        competitors = []

    message = _build_message(model_name, stock_qty, stock_state, our_price, competitors)
    try:
        sent = await _send_kakao_text(db, message)
    except Exception as e:
        # 카카오 발송 실패(네트워크/DNS 등)가 여기서 안 잡히면 예외가 get_inventory_status()를
        # 거쳐 고객에게 갔어야 할 재고 응답 전체를 범용 오류 메시지로 대체해버린다(코드리뷰 H9).
        # 관리자 알림은 부가 기능이므로 실패해도 고객 응답에는 영향 주지 않아야 한다.
        logger.error(f"[카카오알림] 발송 중 예외: {e}")
        sent = False

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
    return sent