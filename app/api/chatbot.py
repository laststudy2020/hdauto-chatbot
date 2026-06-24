from app.core.admin_commands import is_admin_command, handle_admin_command
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.models.schemas import ChatRequest, ChatResponse
from app.core.intent import classify_intent, Intent
from app.services.replacement import find_replacement
from app.services.specs import lookup_specs
from app.services.location import get_location_response
from app.services.inventory import get_inventory_status
from app.services.alarm import diagnose_alarm
from app.services.spec_search import find_by_spec
from app.services.servo_spec_search import find_servo_by_capacity, find_servo_drive_details, find_drives_compatible_with_motor
from app.services.web_search import search_and_answer
from app.core.clova import clova_client, SYSTEM_PROMPTS
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chatbot"])

COMPANY_PHONE = "051-000-0000"


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest, db: AsyncSession = Depends(get_db)):
    message = request.message.strip()
    logger.info(f"[{request.channel}] 사용자({request.user_id}): {message}")

    # ─── 관리자 등록 명령어 — 의도 분류기를 거치지 않고 바로 처리 ───
    if is_admin_command(message):
        try:
            reply = await handle_admin_command(message, db)
            source = "admin_command"
        except Exception as e:
            logger.error(f"관리자 명령 처리 오류: {e}", exc_info=True)
            reply = "명령 처리 중 오류가 발생했습니다."
            source = "error"
        logger.info(f"응답 ({source}): {reply[:80]}...")
        return ChatResponse(
            reply=reply,
            intent="admin_command",
            model_name=None,
            confidence=1.0,
            source=source,
        )

    intent_result = classify_intent(message)
    logger.info(
        f"의도: {intent_result.intent.value} | "
        f"모델: {intent_result.model_name} | "
        f"알람: {intent_result.alarm_code} | "
        f"신뢰도: {intent_result.confidence:.2f}"
    )

    try:
        reply, source = await _route(intent_result, message, db)
    except Exception as e:
        logger.error(f"서비스 오류: {e}", exc_info=True)
        reply = (
            "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.\n"
            f"급한 문의는 현대자동화({COMPANY_PHONE})로 전화 부탁드립니다."
        )
        source = "error"

    logger.info(f"응답 ({source}): {reply[:80]}...")
    return ChatResponse(
        reply=reply,
        intent=intent_result.intent.value,
        model_name=intent_result.model_name,
        confidence=intent_result.confidence,
        source=source,
    )


async def _web_fallback(query: str, intent: str) -> tuple[str, str]:
    """DB 없을 때 웹 검색 → 실패시 HyperCLOVA 자체 지식으로 폴백"""
    try:
        reply, source = await search_and_answer(query=query, intent=intent)
        return reply, f"web_{source}"
    except Exception as e:
        logger.warning(f"웹 검색 실패 → HyperCLOVA 자체 지식 사용: {e}")
        reply = await clova_client.chat_completion(
            system_prompt=SYSTEM_PROMPTS.get(intent, SYSTEM_PROMPTS["general"]),
            user_message=(
                f"FA 부품 전문 지식으로 아래 질문에 답하세요. "
                f"불확실한 정보는 반드시 '불확실' 또는 '공식 확인 필요'로 표시하세요.\n\n"
                f"질문: {query}"
            ),
            temperature=0.3,
        )
        return reply, "clova_knowledge"


async def _route(intent_result, message: str, db: AsyncSession) -> tuple[str, str]:
    intent = intent_result.intent
    model = intent_result.model_name

    # ─── 단종 대체품 ───
    if intent == Intent.REPLACEMENT:
        if model:
            db_reply = await find_replacement(model, db)
            if "찾지 못했습니다" in db_reply or "등록된 대체품 정보가 없습니다" in db_reply:
                logger.info(f"DB 없음 → 웹검색: {model} 대체품")
                return await _web_fallback(f"{model} 단종 대체품 FA 부품", "replacement")
            return db_reply, "db"
        return "어떤 모델의 대체품을 찾고 계신가요?\n모델명을 입력해 주세요.\n예) FX3U-32MT 대체품", "guide"

    # ─── 규격/스펙 ───
    if intent == Intent.SPECS:
        if model:
            # 1) 서보드라이브 모델이면 단종/대체품+호환모터+타사비교를 통합 안내
            servo_detail = await find_servo_drive_details(model, db)
            if servo_detail is not None:
                return servo_detail, "db_servo_detail"

            # 2) 서보모터 모델이면 호환 가능한 드라이브를 역검색
            motor_detail = await find_drives_compatible_with_motor(model, db)
            if motor_detail is not None:
                return motor_detail, "db_servo_motor_reverse"

            # 3) 그 외 일반 제품 스펙 조회
            db_reply = await lookup_specs(model, db)
            if "찾지 못했습니다" in db_reply:
                logger.info(f"DB 없음 → 웹검색: {model} 스펙")
                return await _web_fallback(f"{model} 규격 사양 치수 스펙 FA 부품", "specs")
            return db_reply, "db"
        return "어떤 제품의 규격을 확인하고 싶으신가요?\n모델명을 입력해 주세요.\n예) FX5U-32MT 사이즈", "guide"

    # ─── 사양으로 모델 추천 (역검색, 인버터: 전압+kW) ───
    if intent == Intent.SPEC_SEARCH:
        db_reply = await find_by_spec(
            intent_result.voltage_v, intent_result.capacity_kw, db
        )
        return db_reply, "db_spec_search"

    # ─── 서보드라이브 용량(W) 추천 ───
    if intent == Intent.SERVO_RECOMMEND:
        reply = await find_servo_by_capacity(intent_result.capacity_w, db)
        return reply, "db_servo_spec"

    # ─── 고장 알람 ───
    if intent == Intent.ALARM:
        alarm = intent_result.alarm_code
        db_reply, matched = await diagnose_alarm(
            alarm_code=alarm, model_name=model,
            user_message=message, db=db,
        )
        if not matched:
            logger.info(f"DB 매칭 실패 → 웹검색: {alarm} 알람")
            search_q = f"{model or 'FA 인버터 서보'} {alarm or message} 알람 원인 해결방법"
            return await _web_fallback(search_q, "alarm")
        return db_reply, "db_alarm"

    # ─── 위치 안내 ───
    if intent == Intent.LOCATION:
        return get_location_response(), "fixed"

    # ─── 재고 확인 ───
    if intent == Intent.STOCK:
        if model:
            return await get_inventory_status(model, db), "db"
        return "어떤 제품의 재고를 확인하고 싶으신가요?\n모델명을 입력해 주세요.", "guide"

    # ─── 일반 문의 → 웹 검색 ───
    return await _web_fallback(f"FA 자동화 부품 {message}", "general")
