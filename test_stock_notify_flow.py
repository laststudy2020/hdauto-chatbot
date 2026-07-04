"""
재고 조회 → 카카오 알림 발송까지 실제 흐름을 검증하는 테스트 스크립트.
DB에서 재고가 0이거나 임계값 이하인 제품을 하나 찾아서 get_inventory_status를 호출한다.

실행 위치: 프로젝트 루트
사용법: python test_stock_notify_flow.py [모델명]
        모델명 생략 시 DB에서 자동으로 재고부족/품절 제품 하나를 찾아서 사용
"""

import asyncio
import sys

from sqlalchemy import select

from app.db.database import async_session
from app.db.models import Product, Inventory
from app.services.inventory import get_inventory_status


async def find_low_stock_model() -> str | None:
    async with async_session() as db:
        stmt = (
            select(Product.model_name)
            .join(Inventory, Inventory.product_id == Product.id)
            .where(Inventory.current_stock <= Inventory.min_threshold)
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalars().first()


async def main():
    model_name = sys.argv[1] if len(sys.argv) > 1 else None

    if not model_name:
        print("모델명이 지정되지 않아 DB에서 재고부족/품절 제품을 자동으로 찾습니다...")
        model_name = await find_low_stock_model()
        if not model_name:
            print("재고부족/품절 상태인 제품을 DB에서 찾지 못했습니다. 모델명을 직접 지정해주세요.")
            print("사용법: python test_stock_notify_flow.py <모델명>")
            return

    print(f"조회할 모델명: {model_name}\n")

    async with async_session() as db:
        reply = await get_inventory_status(model_name, db)

    print("=" * 50)
    print("챗봇 응답 (고객에게 보여지는 내용)")
    print("=" * 50)
    print(reply)
    print("\n" + "=" * 50)
    print("카카오톡 '나와의 채팅방'을 확인해서 알림이 도착했는지 확인해주세요.")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())