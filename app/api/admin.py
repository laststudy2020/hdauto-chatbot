from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.db.models import Inventory, Product
from app.services.inventory import check_low_stock
from app.config import get_settings

router = APIRouter(prefix="/api/admin", tags=["admin"])
settings = get_settings()


@router.get("/inventory", summary="전체 재고 현황 조회")
async def get_all_inventory(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Inventory)
        .options(selectinload(Inventory.product))
        .order_by(Inventory.current_stock)
    )
    result = await db.execute(stmt)
    items = result.scalars().all()

    return [
        {
            "model_name": i.product.model_name,
            "manufacturer": i.product.manufacturer,
            "category": i.product.category,
            "current_stock": i.current_stock,
            "min_threshold": i.min_threshold,
            "warehouse": i.warehouse_location,
            "status": (
                "out_of_stock" if i.current_stock == 0
                else "low_stock" if i.current_stock <= i.min_threshold
                else "ok"
            ),
        }
        for i in items
    ]


@router.get("/inventory/low-stock", summary="재고 부족 제품만 조회")
async def get_low_stock(db: AsyncSession = Depends(get_db)):
    alerts = await check_low_stock(db)
    return {
        "count": len(alerts),
        "items": alerts,
    }


@router.post("/inventory/alert", summary="재고 부족 슬랙 알림 발송")
async def trigger_stock_alert(
    slack_webhook_url: str,
    db: AsyncSession = Depends(get_db)
):
    alerts = await check_low_stock(db)
    if not alerts:
        return {"status": "ok", "message": "재고 부족 제품 없음"}
    pass  # 슬랙 알림은 inventory._notify_admin에서 처리
    return {"status": "sent", "count": len(alerts)}


@router.put("/inventory/{model_name}", summary="재고 수량 업데이트")
async def update_stock(
    model_name: str,
    current_stock: int,
    db: AsyncSession = Depends(get_db)
):
    product_res = await db.execute(
        select(Product).where(Product.model_name.ilike(f"%{model_name}%"))
    )
    product = product_res.scalars().first()
    if not product:
        return {"error": f"'{model_name}' 제품 없음"}

    inv_res = await db.execute(
        select(Inventory).where(Inventory.product_id == product.id)
    )
    inv = inv_res.scalar_one_or_none()
    if not inv:
        return {"error": "재고 정보 없음. 먼저 CSV로 재고를 등록해주세요."}

    inv.current_stock = current_stock
    await db.commit()

    is_low = current_stock <= inv.min_threshold
    return {
        "model_name": product.model_name,
        "current_stock": current_stock,
        "min_threshold": inv.min_threshold,
        "alert_needed": is_low,
    }
