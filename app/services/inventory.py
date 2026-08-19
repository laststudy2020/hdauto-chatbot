from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Inventory, Product, Replacement, ProductStatus
from app.config import get_settings
from app.services.naver_commerce import search_stock_by_model_name, NaverCommerceError
from app.services.servo_spec_search import get_servo_companion_note
from app.services.admin_notify import notify_admins
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

CATEGORY_MAP = {
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


def _is_used_stock(product: Product | None) -> bool:
    """보유분이 중고라고 데이터가 말하는가.

    "단종인데 재고가 있으니 중고"라고 추론하지 않는다 — 신품 데드스톡을 중고로
    잘못 안내하면 가격·품질 기대가 달라진다. extra_specs.stock_condition이
    'used'로 명시된 경우에만 참. replacement._build_stock_note()와 같은 기준.
    """
    if not product or not product.specs or not product.specs.extra_specs:
        return False
    return product.specs.extra_specs.get("stock_condition") == "used"


def _build_product_info(product: Product) -> str:
    """카테고리 + 간략 동작사양 조합."""
    lines = []

    # 카테고리 (None/미분류 안전 처리)
    raw_category = getattr(product, "category", None)
    category = CATEGORY_MAP.get(raw_category, raw_category) if raw_category else "미분류"
    manufacturer = product.manufacturer or ""
    lines.append(f"📋 제품 분류: {manufacturer} {category}".strip())

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

    2026-07-02 변경: origin_product_no 사전 매핑 / inventory_sync_enabled 게이트를
    제거하고, 모델명 기반 실시간 검색(search_stock_by_model_name)을 우선 사용.
    사전 매핑이 잘못되어(finalize_matching.py의 오매칭 제외 로직 등) 실제로는
    재고가 있는데도 "재고 없음"으로 응답하는 문제를 막기 위함.

    Returns: (재고수량, "naver" 또는 "db")
    """
    db_quantity = inv.current_stock if inv else 0

    if not settings.NAVER_COMMERCE_ENABLED:
        logger.info(f"[재고조회] NAVER_COMMERCE_ENABLED=False → DB 폴백 ({db_quantity})")
        return db_quantity, "db"

    model_name = getattr(product, "model_name", None)
    if not model_name:
        logger.info("[재고조회] model_name 없음 → DB 폴백")
        return db_quantity, "db"

    try:
        result = await search_stock_by_model_name(model_name)
    except NaverCommerceError as e:
        logger.warning(f"[재고조회] 네이버 모델명 검색 실패, DB 값으로 대체: {model_name} - {e}")
        return db_quantity, "db"

    if result is None:
        logger.info(f"[재고조회] {model_name} | 모델명 검색 결과 없음 → DB 폴백 ({db_quantity})")
        return db_quantity, "db"

    logger.info(
        f"[재고조회] {model_name} | 모델명 검색 매칭='{result['matched_name']}' "
        f"({result['match_count']}건) | 재고={result['quantity']}"
    )
    return result["quantity"], "naver"


async def get_stock_state(product: Product, db: AsyncSession) -> dict:
    """제품(Product row 존재)의 재고 상태를 단일 기준으로 판정."""
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


async def _check_naver_directly(model_name: str) -> dict | None:
    """
    Product 테이블에 등록되지 않은 모델명에 대해서도, 네이버 실시간 검색으로
    바로 재고를 확인한다 (DB 카탈로그 등록 여부와 무관하게 스마트스토어에
    실제로 있으면 잡아내기 위함).

    Returns:
        {"quantity": int, "matched_name": str} 또는 None (검색 비활성/실패/미매칭)
    """
    if not settings.NAVER_COMMERCE_ENABLED:
        return None

    try:
        result = await search_stock_by_model_name(model_name)
    except NaverCommerceError as e:
        logger.warning(f"[재고조회] DB미등록 모델 네이버 직접검색 실패: {model_name} - {e}")
        return None

    if result is None:
        return None

    logger.info(
        f"[재고조회] DB미등록 모델 '{model_name}' | 네이버 직접검색 매칭='{result['matched_name']}' "
        f"| 재고={result['quantity']}"
    )
    return {"quantity": result["quantity"], "matched_name": result["matched_name"]}


async def get_inventory_status(model_name: str, db: AsyncSession) -> str:
    """재고 조회 → 재고여부 + 카테고리/사양 + 단종시 대체품(주의사항 포함)"""

    model_name = (model_name or "").strip()
    if not model_name:
        return (
            f"모델명을 인식하지 못했습니다. 정확한 모델명을 입력해 주세요.\n"
            f"📞 문의: {COMPANY_PHONE}"
        )

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
        # DB 카탈로그에 없는 모델 → 바로 out_of_stock 단정하지 않고
        # 네이버 실시간 검색으로 한 번 더 확인 (스마트스토어에 실제로
        # 있는 상품인데 DB에만 안 등록된 경우를 놓치지 않기 위함)
        direct = await _check_naver_directly(model_name)
        if direct is None:
            # 스마트스토어에서도 상품 자체를 못 찾음 → 재고 유무를 단정할 수 없다.
            # "재고 없음"으로 단정하면 카탈로그에 없는 취급 모델의 주문을 놓친다.
            stock = {"quantity": 0, "source": "none", "state": "unknown", "min_threshold": 0}
        elif direct["quantity"] > 0:
            stock = {
                "quantity": direct["quantity"],
                "source": "naver",
                "state": "in_stock",
                "min_threshold": settings.DEFAULT_STOCK_THRESHOLD,
            }
            product_name = direct["matched_name"]
        else:
            # 스마트스토어에 상품은 있는데 재고가 0 → 진짜 품절
            stock = {"quantity": 0, "source": "naver", "state": "out_of_stock", "min_threshold": 0}
            product_name = direct["matched_name"]

    # 3-1) 카테고리 + 사양 정보 (Product row가 있을 때만 표시 가능)
    product_info = _build_product_info(product) if product else ""

    # 3-2) 서보 호환 정보 (조회 실패해도 전체 흐름은 안 끊기게 방어)
    try:
        companion_note = await get_servo_companion_note(product, model_name, db)
    except Exception as e:
        logger.warning(f"서보 호환 정보 조회 실패: {model_name} - {e}")
        companion_note = ""
    companion_note = companion_note or ""

    # 3-3) description 특이사항 (버전별 안내 등)
    desc_note = ""
    if product and product.description:
        desc_note = f"ℹ️ {product.description}\n\n"

    # 3-4) Product 없는데 서보모터로 식별된 경우
    if not product and companion_note:
        if stock["quantity"] > 0:
            stock_label = (
                "✅ 재고 있음 (소진 임박 — 서두르시는 걸 권장드립니다)"
                if stock["state"] == "low_stock"
                else "✅ 재고 있음"
            )
            return (
                f"{stock_label}\n\n"
                f"'{model_name}'은(는) 당사 카탈로그에 별도 등록된 모델은 아니지만, "
                f"서보모터로 확인됩니다.{companion_note}\n\n"
                f"🛒 스마트스토어에서 바로 구매 가능합니다.\n"
                f"{STORE_URL}"
            )

        return (
            f"'{model_name}'은(는) 당사 카탈로그에 별도 등록된 모델은 아니지만, "
            f"서보모터로 확인됩니다.{companion_note}\n\n"
            f"📞 정확한 재고/사양은 현대자동화로 문의해 주세요.\n"
            f"☎️ {COMPANY_PHONE}"
        )

    # ── 단종 여부 ──
    is_discontinued = bool(product and product.status == ProductStatus.DISCONTINUED)
    disc_label = "\n⚠️ 단종 제품 — 재고 소진 후 구매 불가합니다." if is_discontinued else ""

    # ── 대체품 안내 블록 (단종이거나 재고 없을 때) ──
    async def _build_replacement_block() -> str:
        """DB에 등록된 대체품만 안내한다.

        예전엔 여기서 웹검색(search_and_answer) 결과를 '유사 사양 제품 안내'로
        덧붙였다. DB 답이 이미 있어도 무조건 붙는 구조라, 한 응답 안에 큐레이션된
        정답과 생성된 오답이 나란히 나갔다. 2026-08-19 프로덕션 실측: SV015iG5A-4
        재고 응답이 정답 'LSLV0015G100-4' 바로 아래에 실재하지 않는 형명
        'S130IS7시리즈'를 추천했고, 그쪽이 더 길고 구체적이라 고객은 그걸 읽는다.

        chatbot._route의 폴백 체인에는 웹/LLM이 DB 응답을 덮어쓰지 못하게 하는
        가드가 있지만, 이 호출은 폴백이 아니라 상시 추가여서 가드를 비켜갔다.
        대체품을 더 넓게 찾아야 하는 질문이면 replacement 의도로 라우팅되는 게 맞다.
        """
        if not db_replacements:
            return ""
        return f"\n\n🔄 추천 대체 모델: {', '.join(db_replacements)}" + REPLACEMENT_CAUTION

    # ────────────────────────────────────────────
    # 3-5) 재고 확인 불가 (DB·스마트스토어 모두 미매칭)
    # ────────────────────────────────────────────
    if stock["state"] == "unknown":
        # 재고 유무만 단정하지 않을 뿐, 대체품 안내 자체는 고객에게 유용하므로 유지한다.
        # 관리자 알림은 보내지 않는다 — 매칭에 성공한 조회만 알림 대상이다.
        replacement_block = await _build_replacement_block()
        return (
            f"{desc_note}"
            f"🔎 '{model_name}'은(는) 정확한 재고 확인을 위해 확인 후 안내드리겠습니다.\n\n"
            f"{companion_note}"
            f"{replacement_block}\n\n"
            f"📞 현대자동화로 연락주시면 바로 확인해 드리겠습니다.\n"
            f"☎️ {COMPANY_PHONE}"
        )

    # ────────────────────────────────────────────
    # 4) 재고 없음
    # ────────────────────────────────────────────
    if stock["state"] == "out_of_stock":
        await _notify_admin(product_name)
        await notify_admins(db, product, product_name, stock["quantity"], "out_of_stock")
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
    # 단종품 보유분이 중고인 경우 '재고 있음'만 쓰면 고객은 신품으로 읽는다.
    # iG5A처럼 신품 단종 + 중고 재고 판매 중인 품목이 실제로 있으므로 명시한다.
    used = _is_used_stock(product)
    stock_word = "중고 재고 있음" if used else "재고 있음"
    if stock["state"] == "low_stock":
        stock_label = f"✅ {stock_word} (소진 임박 — 서두르시는 걸 권장드립니다)"
    else:
        stock_label = f"✅ {stock_word}"
    if used:
        disc_label = (
            "\n⚠️ 신품은 단종됐고, 보유분은 중고입니다 "
            "(상태는 문의해 주세요). 소진 후 구매 불가합니다."
        )

    # 재고 조회 의도로 매칭에 성공한 경우 매번 알림. 재고가 충분한 문의도
    # "어떤 상품을 고객이 찾고 있는가"라는 수요 신호로 쓴다. 발송량은
    # notify_admins의 모델별 1시간 디바운스가 억제한다.
    await notify_admins(db, product, product_name, stock["quantity"], stock["state"])

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
            async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
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