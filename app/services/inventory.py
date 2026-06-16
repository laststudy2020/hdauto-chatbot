from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Inventory, Product, Replacement, ProductStatus
from app.config import get_settings
from app.services.web_search import search_and_answer
import httpx
import logging

logger = logging.getLogger(__name__)
settings = get_settings()

COMPANY_PHONE = "010-3861-2030"
STORE_URL = "https://smartstore.naver.com/hdauto22"


async def get_inventory_status(model_name: str, db: AsyncSession) -> str:
    """재고 조회 → 재고있음/없음 + 단종여부 + 대체품 안내"""

    # 1) 재고 DB 조회
    stmt = (
        select(Inventory)
        .options(selectinload(Inventory.product))
        .join(Product)
        .where(Product.model_name.ilike(f"%{model_name}%"))
    )
    result = await db.execute(stmt)
    inv = result.scalars().first()

    # 2) 제품 정보 조회 (단종 여부)
    prod_stmt = select(Product).where(Product.model_name.ilike(f"%{model_name}%"))
    prod_result = await db.execute(prod_stmt)
    product = prod_result.scalars().first()

    # 3) 대체품 조회
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

    # 4) 재고 없음 (DB에 재고 정보 없거나 품절)
    if not inv or inv.current_stock == 0:
        product_name = inv.product.model_name if inv else model_name

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

    # 5) 재고 있음
    disc_info = ""
    rep_info = ""

    if product and product.status == ProductStatus.DISCONTINUED:
        disc_info = f"\n⚠️ 단종 예정 제품입니다."
        if replacement_info:
            rep_info = replacement_info

    return (
        f"✅ '{inv.product.model_name}' 재고 있음{disc_info}"
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
