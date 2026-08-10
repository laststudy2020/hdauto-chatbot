"""S100 중량/치수 적재값 점검 — 라이브 테스트가 기대한 5.4kg과 어긋난 건 확인."""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from app.db.database import async_session
from app.db.models import Product, Specification


async def main() -> None:
    async with async_session() as db:
        rows = (await db.execute(
            select(Product.model_name, Specification.rated_power,
                   Specification.weight_kg, Specification.dimension_w,
                   Specification.dimension_h, Specification.dimension_d)
            .join(Specification, Specification.product_id == Product.id)
            .where(Product.series == "LSLV-S100")
            .order_by(Product.model_name)
        )).all()
        print(f"=== LSLV-S100 {len(rows)}건 ===")
        for name, power, w, dw, dh, dd in rows:
            print(f"  {name:20} 출력={str(power):8} 중량={str(w):6} "
                  f"WxHxD={dw}x{dh}x{dd}")


if __name__ == "__main__":
    asyncio.run(main())
