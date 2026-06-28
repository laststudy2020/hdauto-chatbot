from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Inventory, Product, Replacement, ProductStatus
from app.config import get_settings
from app.services.web_search import search_and_answer
from app.services.naver_commerce import get_live_stock_quantity, NaverCommerceError
import httpx
import logging

logger = logging.getLogger(__name__)
settings = get_settings()

COMPANY_PHONE = "010-3861-2030"
STORE_URL = "https://smartstore.naver.com/hdauto22"


async def _resolve_stock_quantity(product: Product | None, inv: Inventory | None) -> tuple[int, str]:
    """재고 수량의 단일 진입점.

    오늘(DB 기준): NAVER_COMMERCE_ENABLED=False 라서 항상 DB 값(inv.current_stock) 반환.
    월요일(API 연동 후): NAVER_COMMERCE_ENABLED=True + smartstore_product_id가 있으면
    네이버 커머스API로 실시간 조회. 실패 시 예외를 잡아 DB 값으로 자동 폴백.

    이 함수 밖(get_inventory_status)의 호출 코드는 전혀 바꿀 필요 없음 —
    데이터 소스를 바꿔치기하는 지점은 여기 하나로 고정.

    Returns: (재고수량, "naver" 또는 "db")
    """
    db_quantity = inv.current_stock if inv else 0

    if not settings.NAVER_COMMERCE_ENABLED:
        return db_quantity, "db"

    if not product or not product.smartstore_product_id:
        return db_quantity, "db"

    try:
        qty = await get_live_stock_quantity(product.smartstore_product_id)
        return qty, "naver"
    except NaverCommerceError as e:
        logger.warning(
            f"네이버 실시간 재고 조회 실패, DB 값으로 대체: "
            f"{product.model_name} ({product.smartstore_product_id}) - {e}"
        )
        return db_quantity, "db"


async def get_stock_state(product: Product, db: AsyncSession) -> dict:
    """제품의 재고 상태를 단일 기준으로 판정.

    실제 수량 조회는 _resolve_stock_quantity()에 위임(네이버 실시간/DB 폴백 동일 로직).
    이 함수를 STOCK 의도(get_inventory_status)와 REPLACEMENT 의도(find_replacement)
    양쪽에서 공통으로 써서, "재고 있어요?"든 "이거 단종됐나요?"든 질문 문구가 달라도
    항상 같은 재고 판단(재고있음/부족/없음)이 나오게 한다.

    Returns: {"quantity": int, "source": "naver"|"db",
              "state": "in_stock"|"low_stock"|"out_of_stock", "min_threshold": int}
    """
    inv_stmt = select(Inventory).where(Inventory.product_id == product.id)
    inv_result = await db.execute(inv_stmt)
    inv = inv_result.scalars().first()

    quantity, source = await _resolve_stock_quantity(product, inv)
    min_threshold = inv.min_threshold if inv else settings.DEFAULT_STOCK_THRESHOLD

    if quantity == 0:
        state = "out_of_stock"
    elif quantity <= min_threshold:
        state = "low_stock"
    else:
        state = "in_stock"

    return {
        "quantity": quantity,
        "source": source,
        "state": state,
        "min_threshold": min_threshold,
    }


async def get_inventory_status(model_name: str, db: AsyncSession) -> str:
    """재고 조회 → 재고있음/부족/없음 + 단종여부 + 대체품 안내"""

    # 1) 제품 정보 조회
    prod_stmt = select(Product).where(Product.model_name.ilike(f"%{model_name}%"))
    prod_result = await db.execute(prod_stmt)
    product = prod_result.scalars().first()

    # 2) 대체품 조회
    replacement_info = ""
    if product:
        rep_stmt = (
            select(Replacement)
            .options(selectinload(Replacement.new_product))
            .where(Replacement.old_model_id == product.id)
        )
        rep_result = await db.execute(rep_stmt)
        replacements = rep_result.scalars().all()
        if replacements:
            rep_names = ", ".join(r.new_product.model_name for r in replacements[:2])
            replacement_info = f"\n\n🔄 대체 추천 모델: {rep_names}"

    # 3) 재고 상태 판정 (네이버 실시간 우선, DB 폴백 — 공통 로직, REPLACEMENT 의도와 동일)
    product_name = product.model_name if product else model_name
    if product:
        stock = await get_stock_state(product, db)
    else:
        stock = {"quantity": 0, "source": "db", "state": "out_of_stock", "min_threshold": 0}

    # 4) 재고 없음 (DB/매칭 실패 포함)
    if stock["state"] == "out_of_stock":
        # 관리자 알림 발송
        await _notify_admin(product_name)

        # 웹 검색으로 대체품 찾기 (DB에 없을 경우)
        if not replacement_info:
            try:
                web_reply, _ = await search_and_answer(
                    query=f"{model_name} 단종 대체품 FA 부품",
                    intent="replacement"
                )
                replacement_info = f"\n\n🔄 대체품 안내:\n{web_reply[:300]}"
            except Exception as e:
                logger.warning(f"대체품 웹 검색 실패: {e}")

        # 단종 여부 표시
        disc_info = ""
        if product and product.status == ProductStatus.DISCONTINUED:
            disc_info = f"\n⚠️ 해당 제품은 단종 제품입니다."

        return (
            f"📦 '{product_name}' 재고 없음{disc_info}"
            f"{replacement_info}\n\n"
            f"📞 현대자동화에 연락주시면 재고 파악과 대체품 안내해드리겠습니다.\n"
            f"☎️ {COMPANY_PHONE}"
        )

    # 5) 재고 부족/있음 공통 정보
    disc_info = ""
    rep_info = ""
    if product and product.status == ProductStatus.DISCONTINUED:
        disc_info = f"\n⚠️ 단종 제품입니다. 재고 소진 후 구매 불가."
        rep_info = replacement_info  # 단종이면 대체품 항상 표시

    # 5-1) 재고 부족 (소진 임박 — 있음/없음 사이 중간 상태)
    if stock["state"] == "low_stock":
        return (
            f"⏳ '{product_name}' 재고 {stock['quantity']}개 남음 (소진 임박){disc_info}"
            f"{rep_info}\n\n"
            f"🛒 서두르시는 걸 추천드려요 — 스마트스토어에서 바로 구매 가능합니다.\n"
            f"{STORE_URL}"
        )

    # 6) 재고 있음
    return (
        f"✅ '{product_name}' 재고 있음{disc_info}"
        f"{rep_info}\n\n"
        f"🛒 스마트스토어에서 바로 구매 가능합니다.\n"
        f"{STORE_URL}"
    )


async def _notify_admin(model_name: str):
    """재고 없음 시 관리자에게 카카오톡/슬랙 알림"""
    message = f"[재고 없음 알림] 고객이 찾는 제품이 재고가 없습니다.\n제품명: {model_name}"

    # 슬랙 알림 (설정된 경우)
    if settings.SLACK_WEBHOOK_URL:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    settings.SLACK_WEBHOOK_URL,
                    json={"text": message}
                )
            logger.info(f"관리자 알림 발송: {model_name}")
        except Exception as e:
            logger.warning(f"관리자 알림 실패: {e}")
    else:
        logger.info(f"[관리자 알림 대기] {message}")


async def check_low_stock(db: AsyncSession) -> list[dict]:
    """재고 임계값 이하 제품 조회"""
    stmt = (
        select(Inventory)
        .options(selectinload(Inventory.product))
        .where(Inventory.current_stock <= Inventory.min_threshold)
    )
    result = await db.execute(stmt)
    low_items = result.scalars().all()

    alerts = []
    for inv in low_items:
        alerts.append({
            "model_name": inv.product.model_name,
            "manufacturer": inv.product.manufacturer,
            "category": inv.product.category,
            "current_stock": inv.current_stock,
            "min_threshold": inv.min_threshold,
            "status": "out_of_stock" if inv.current_stock == 0 else "low_stock",
        })
    return alerts
