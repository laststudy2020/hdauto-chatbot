from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Inventory, Product, StockAlert, AlertChannel
from app.config import get_settings
from datetime import datetime
import httpx
import logging

logger = logging.getLogger(__name__)
settings = get_settings()


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
            "warehouse": inv.warehouse_location,
            "status": "out_of_stock" if inv.current_stock == 0 else "low_stock",
        })
    return alerts


async def send_slack_alert(alerts: list[dict], webhook_url: str):
    """슬랙 웹훅으로 재고 부족 알림 발송"""
    if not alerts or not webhook_url:
        return

    lines = ["*[HD AUTO] 재고 부족 알림*\n"]
    for a in alerts:
        icon = "🔴" if a["status"] == "out_of_stock" else "🟡"
        lines.append(
            f"{icon} *{a['model_name']}* ({a['manufacturer']})\n"
            f"   현재: {a['current_stock']}개 | 최소: {a['min_threshold']}개 | 위치: {a['warehouse']}"
        )

    payload = {"text": "\n".join(lines)}
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(webhook_url, json=payload)
    logger.info(f"슬랙 알림 발송: {len(alerts)}개 제품")


async def get_inventory_status(model_name: str, db: AsyncSession) -> str:
    """챗봇용 재고 현황 응답 생성"""
    stmt = (
        select(Inventory)
        .options(selectinload(Inventory.product))
        .join(Product)
        .where(Product.model_name.ilike(f"%{model_name}%"))
    )
    result = await db.execute(stmt)
    inv = result.scalars().first()

    if not inv:
        return (
            f"'{model_name}' 재고 정보가 등록되어 있지 않습니다.\n"
            f"현대자동화({settings.COMPANY_PHONE})로 문의해 주세요."
        )

    if inv.current_stock == 0:
        return (
            f"'{inv.product.model_name}' 현재 재고 없음(품절)\n"
            f"입고 문의: {settings.COMPANY_PHONE}"
        )
    elif inv.current_stock <= inv.min_threshold:
        return (
            f"'{inv.product.model_name}' 재고 {inv.current_stock}개 (소량 남음)\n"
            f"빠른 주문을 권장합니다. 문의: {settings.COMPANY_PHONE}"
        )
    else:
        return (
            f"'{inv.product.model_name}' 재고 있음 ({inv.current_stock}개)\n"
            f"스마트스토어에서 바로 구매 가능합니다."
        )
