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

# 대체품 교체 시 공통 주의사항
REPLACEMENT_CAUTION = (
    "\n\n⚠️ 대체품 교체 시 반드시 확인하세요:\n"
    "1. 파라미터 설정값 재조정 필요 (기존 백업 권장)\n"
    "2. 배선 도면 및 커넥터 핀 배치 재확인\n"
    "3. 정격 전압/전류/용량 등 동작 사양 일치 여부 확인\n"
    "4. 통신 프로토콜 및 네트워크 호환 여부 확인 (CC-Link, EtherCAT 등)\n"
    "5. 기계 치수 및 마운팅 방식 호환 여부 확인"
)


def _build_product_info(product: Product) -> str:
    """카테고리 + 간략 동작사양 조합."""
    lines = []

    # 카테고리
    category_map = {
        "servo": "서보드라이브",
        "서보드라이브": "서보드라이브",
        "inverter": "인버터",
        "인버터": "인버터",
        "servo_motor": "서보모터",
        "서보모터": "서보모터",
        "plc": "PLC",
        "PLC": "PLC",
        "hmi": "HMI(터치스크린)",
        "HMI": "HMI(터치스크린)",
    }
    category = category_map.get(product.category, product.category)
    manufacturer = product.manufacturer or ""
    lines.append(f"📋 제품 분류: {manufacturer} {category}")

    # 스펙 정보 (있는 항목만 출력)
    specs = product.specs
    if specs:
        spec_items = []
        if specs.rated_power:
            spec_items.append(f"정격출력 {specs.rated_power}")
        if specs.input_voltage:
            spec_items.append(f"입력전압 {specs.input_voltage}")
        if specs.comm_protocol:
            spec_items.append(f"통신 {specs.comm_protocol}")
        if specs.io_points:
            spec_items.append(f"I/O {specs.io_points}")
        if spec_items:
            lines.append(f"⚙️ 주요 사양: {' | '.join(spec_items)}")

    return "\n".join(lines)


async def _resolve_stock_quantity(product: Product | None, inv: Inventory | None) -> tuple[int, str]:
    """재고 수량의 단일 진입점.

    Returns: (재고수량, "naver" 또는 "db")
    """
    db_quantity = inv.current_stock if inv else 0

    if not settings.NAVER_COMMERCE_ENABLED:
        logger.info(f"[재고조회] NAVER_COMMERCE_ENABLED=False → DB 폴백 ({db_quantity})")
        return db_quantity, "db"

    if not product or not getattr(product, "inventory_sync_enabled", True):
        logger.info(
            f"[재고조회] {getattr(product, 'model_name', '?')} | "
            f"inventory_sync_enabled={getattr(product, 'inventory_sync_enabled', None)} → DB 폴백"
        )
        return db_quantity, "db"

    product_no = getattr(product, "origin_product_no", None) or product.smartstore_product_id

    logger.info(
        f"[재고조회] {product.model_name} | "
        f"sync_enabled={getattr(product, 'inventory_sync_enabled', None)} | "
        f"origin_no={getattr(product, 'origin_product_no', None)} | "
        f"product_no={product_no} | db_stock={db_quantity}"
    )

    if not product_no:
        logger.warning(f"[재고조회] {product.model_name} | origin_product_no 없음 → DB 폴백")
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
    """재고 조회 → 재고여부 + 카테고리/사양 + 단종시 대체품(주의사항 포함)"""

    # 1) 제품 정보 조회 (specs eager load)
    prod_stmt = (
        select(Product)
        .options(selectinload(Product.specs))
        .where(Product.model_name.ilike(f"%{model_name}%"))
    )
    prod_result = await db.execute(prod_stmt)
    product = prod_result.scalars().first()

    # 2) DB 대체품 조회
    db_replacements: list[str] = []
    if product:
        rep_stmt = (
            select(Replacement)
            .options(selectinload(Replacement.new_product))
            .where(Replacement.old_model_id == product.id)
        )
        rep_result = await db.execute(rep_stmt)
        replacements = rep_result.scalars().all()
        db_replacements = [r.new_product.model_name for r in replacements[:2]]

    # 3) 재고 상태 판정
    product_name = product.model_name if product else model_name
    if product:
        stock = await get_stock_state(product, db)
    else:
        stock = {"quantity": 0, "source": "db", "state": "out_of_stock", "min_threshold": 0}

    # 3-1) 카테고리 + 사양 정보
    product_info = _build_product_info(product) if product else ""

    # 3-2) 서보 호환 정보
    companion_note = await get_servo_companion_note(product, model_name, db)

    # 3-3) description 특이사항 (버전별 안내 등)
    desc_note = ""
    if product and product.description:
        desc_note = f"ℹ️ {product.description}\n\n"

    # 3-4) Product 없는데 서보모터로 식별된 경우
    if not product and companion_note:
        return (
            f"'{model_name}'은(는) 당사 카탈로그에 별도 등록된 모델은 아니지만, "
            f"서보모터로 확인됩니다.{companion_note}\n\n"
            f"📞 정확한 재고/사양은 현대자동화로 문의해 주세요.\n"
            f"☎️ {COMPANY_PHONE}"
        )

    # ── 단종 여부 ──
    is_discontinued = product and product.status == ProductStatus.DISCONTINUED
    disc_label = "\n⚠️ 단종 제품 — 재고 소진 후 구매 불가합니다." if is_discontinued else ""

    # ── 대체품 안내 블록 (단종이거나 재고 없을 때) ──
    async def _build_replacement_block() -> str:
        """DB 대체품 우선, 없으면 웹검색으로 타사 비슷한 사양 제품 포함."""
        lines = []
        if db_replacements:
            lines.append(f"🔄 추천 대체 모델: {', '.join(db_replacements)}")

        # 웹검색으로 타사 비슷한 사양 제품 추가 안내
        try:
            web_reply, _ = await search_and_answer(
                query=f"{model_name} 단종 대체품 동급 사양 FA 자동화 부품",
                intent="replacement"
            )
            if web_reply and len(web_reply) > 20:
                lines.append(f"🌐 유사 사양 제품 안내 (참고용):\n{web_reply[:400]}")
        except Exception as e:
            logger.warning(f"대체품 웹 검색 실패: {e}")

        if lines:
            return "\n\n" + "\n\n".join(lines) + REPLACEMENT_CAUTION
        return ""

    # ────────────────────────────────────────────
    # 4) 재고 없음
    # ────────────────────────────────────────────
    if stock["state"] == "out_of_stock":
        await _notify_admin(product_name)
        replacement_block = await _build_replacement_block()

        return (
            f"{desc_note}"
            f"📦 '{product_name}' 재고 없음{disc_label}\n\n"
            f"{product_info}"
            f"{companion_note}"
            f"{replacement_block}\n\n"
            f"📞 현대자동화에 연락주시면 재고 파악과 대체품 안내해드리겠습니다.\n"
            f"☎️ {COMPANY_PHONE}"
        )

    # ────────────────────────────────────────────
    # 5) 재고 있음 (low_stock 포함)
    # ────────────────────────────────────────────
    if stock["state"] == "low_stock":
        stock_label = "✅ 재고 있음 (소진 임박 — 서두르시는 걸 권장드립니다)"
    else:
        stock_label = "✅ 재고 있음"

    # 단종이지만 재고는 남은 경우 → 대체품도 같이 안내
    replacement_block = ""
    if is_discontinued:
        replacement_block = await _build_replacement_block()

    return (
        f"{desc_note}"
        f"{stock_label}{disc_label}\n\n"
        f"{product_info}"
        f"{companion_note}"
        f"{replacement_block}\n\n"
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