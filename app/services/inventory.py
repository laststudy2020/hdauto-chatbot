from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Inventory, Product, Replacement, ProductStatus
from app.config import get_settings
from app.services.web_search import search_and_answer
from app.services.naver_commerce import get_live_stock_quantity, NaverCommerceError
from app.services.servo_spec_search import get_servo_companion_note
import httpx
import logging

logger = logging.getLogger(__name__)
settings = get_settings()

COMPANY_PHONE = "010-3861-2030"
STORE_URL = "https://smartstore.naver.com/hdauto22"


async def _resolve_stock_quantity(product: Product | None, inv: Inventory | None) -> tuple[int, str]:
    """재고 수량의 단일 진입점.

    Returns: (재고수량, "naver" 또는 "db")
    """
    db_quantity = inv.current_stock if inv else 0

    if not settings.NAVER_COMMERCE_ENABLED:
        logger.info(f"[재고조회] NAVER_COMMERCE_ENABLED=False → DB 폴백 ({db_quantity})")
        return db_quantity, "db"

    # inventory_sync_enabled=False 이면 API 호출 없이 DB 값 사용
    if not product or not getattr(product, "inventory_sync_enabled", True):
        logger.info(
            f"[재고조회] {getattr(product, 'model_name', '?')} | "
            f"inventory_sync_enabled={getattr(product, 'inventory_sync_enabled', None)} → DB 폴백"
        )
        return db_quantity, "db"

    # origin_product_no 우선 사용, 없으면 smartstore_product_id 폴백
    product_no = getattr(product, "origin_product_no", None) or product.smartstore_product_id

    # 디버그 로그 — return 이전에 위치해야 실제 출력됨
    logger.info(
        f"[재고조회] {product.model_name} | "
        f"sync_enabled={getattr(product, 'inventory_sync_enabled', None)} | "
        f"origin_no={getattr(product, 'origin_product_no', None)} | "
        f"product_no={product_no} | db_stock={db_quantity}"
    )

    if not product_no:
        logger.warning(f"[재고조회] {product.model_name} | origin_product_no/smartstore_product_id 없음 → DB 폴백")
        return db_quantity, "db"

    try:
        qty = await get_live_stock_quantity(product_no)
        logger.info(f"[재고조회] {product.model_name} | 네이버 실시간 재고={qty}")
        return qty, "naver"

    except NaverCommerceError as e:
        logger.warning(
            f"[재고조회] 네이버 실시간 재고 조회 실패, DB 값으로 대체: "
            f"{product.model_name} ({product_no}) - {e}"
        )
        return db_quantity, "db"


async def get_stock_state(product: Product, db: AsyncSession) -> dict:
    """제품의 재고 상태를 단일 기준으로 판정."""
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
    prod_stmt = (
        select(Product)
        .options(selectinload(Product.specs))
        .where(Product.model_name.ilike(f"%{model_name}%"))
    )
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

    # 3) 재고 상태 판정
    product_name = product.model_name if product else model_name
    if product:
        stock = await get_stock_state(product, db)
    else:
        stock = {"quantity": 0, "source": "db", "state": "out_of_stock", "min_threshold": 0}

    # 3-1) 서보 호환 정보
    companion_note = await get_servo_companion_note(product, model_name, db)

    # 3-2) Product 없는데 서보모터로 식별된 경우
    if not product and companion_note:
        return (
            f"'{model_name}'은(는) 당사 카탈로그에 별도 등록된 모델은 아니지만, "
            f"서보모터로 확인됩니다.{companion_note}\n\n"
            f"📞 정확한 재고/사양은 현대자동화로 문의해 주세요.\n"
            f"☎️ {COMPANY_PHONE}"
        )

    # 4) 재고 없음
    if stock["state"] == "out_of_stock":
        await _notify_admin(product_name)

        if not replacement_info:
            try:
                web_reply, _ = await search_and_answer(
                    query=f"{model_name} 단종 대체품 FA 부품",
                    intent="replacement"
                )
                replacement_info = f"\n\n🔄 대체품 안내:\n{web_reply[:300]}"
            except Exception as e:
                logger.warning(f"대체품 웹 검색 실패: {e}")

        disc_info = ""
        if product and product.status == ProductStatus.DISCONTINUED:
            disc_info = f"\n⚠️ 해당 제품은 단종 제품입니다."

        return (
            f"📦 '{product_name}' 재고 없음{disc_info}"
            f"{replacement_info}{companion_note}\n\n"
            f"📞 현대자동화에 연락주시면 재고 파악과 대체품 안내해드리겠습니다.\n"
            f"☎️ {COMPANY_PHONE}"
        )

    # 5) 재고 부족/있음 공통 정보
    disc_info = ""
    rep_info = ""
    if product and product.status == ProductStatus.DISCONTINUED:
        disc_info = f"\n⚠️ 단종 제품입니다. 재고 소진 후 구매 불가."
        rep_info = replacement_info

    # 5-1) 재고 부족
    if stock["state"] == "low_stock":
        return (
            f"⏳ '{product_name}' 재고 {stock['quantity']}개 남음 (소진 임박){disc_info}"
            f"{rep_info}{companion_note}\n\n"
            f"🛒 서두르시는 걸 추천드려요 — 스마트스토어에서 바로 구매 가능합니다.\n"
            f"{STORE_URL}"
        )

    # 6) 재고 있음
    desc_note = ""
    if product and product.description:
        desc_note = f"ℹ️ {product.description}\n\n"

    return (
        f"{desc_note}✅ '{product_name}' 재고 있음{disc_info}"
        f"{rep_info}{companion_note}\n\n"
        f"🛒 스마트스토어에서 바로 구매 가능합니다.\n"
        f"{STORE_URL}"
    )


async def _notify_admin(model_name: str):
    """재고 없음 시 슬랙 알림"""
    message = f"[재고 없음 알림] 고객이 찾는 제품이 재고가 없습니다.\n제품명: {model_name}"

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