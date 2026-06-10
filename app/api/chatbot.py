from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.models.schemas import ChatRequest, ChatResponse
from app.core.intent import classify_intent, Intent
from app.services.replacement import find_replacement
from app.services.specs import lookup_specs
from app.services.location import get_location_response
from app.services.inventory import get_inventory_status
from app.core.clova import clova_client, SYSTEM_PROMPTS
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chatbot"])


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest, db: AsyncSession = Depends(get_db)):
    message = request.message.strip()
    logger.info(f"[{request.channel}] 사용자({request.user_id}): {message}")

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
            "급한 문의는 현대자동화(051-000-0000)로 전화 부탁드립니다."
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


async def _route(intent_result, message: str, db: AsyncSession) -> tuple[str, str]:
    intent = intent_result.intent
    model = intent_result.model_name

    if intent == Intent.REPLACEMENT:
        if model:
            return await find_replacement(model, db), "db"
        return (
            "어떤 모델의 대체품을 찾고 계신가요?\n"
            "모델명을 입력해 주시면 바로 찾아드리겠습니다.\n"
            "예: 'FX3U-32MT 대체품 알려줘'"
        ), "guide"

    if intent == Intent.SPECS:
        if model:
            return await lookup_specs(model, db), "db"
        return (
            "어떤 제품의 규격을 확인하고 싶으신가요?\n"
            "모델명을 입력해 주세요.\n"
            "예: 'FX5U-32MT 사이즈', 'SV015iG5A-4 스펙'"
        ), "guide"

    if intent == Intent.ALARM:
        alarm = intent_result.alarm_code
        context = f"알람코드: {alarm}" if alarm else ""
        if model:
            context += f"\n제품 모델: {model}"
        reply = await clova_client.chat_completion(
            system_prompt=SYSTEM_PROMPTS["alarm"],
            user_message=(
                f"[증상 정보]\n{context or '(알람코드 미입력)'}\n\n"
                f"[질문]\n{message}\n\n"
                f"매뉴얼 DB는 Phase 2에서 연동 예정입니다. "
                f"현재는 일반 FA 부품 지식으로 안내하되, "
                f"반드시 공식 매뉴얼 확인을 권고해 주세요."
            ),
            temperature=0.2,
        )
        return reply, "clova"

    if intent == Intent.LOCATION:
        return get_location_response(), "fixed"

    if intent == Intent.STOCK:
        if model:
            return await get_inventory_status(model, db), "db"
        return (
            "어떤 제품의 재고를 확인하고 싶으신가요?\n"
            "모델명을 입력해 주세요.\n"
            "예: 'FX5U-32MT 재고 있나요?'"
        ), "guide"

    if intent == Intent.PRICE_COMPARE:
        return (
            "단가 비교 기능은 관리자 대시보드(/api/admin)에서 확인 가능합니다."
        ), "fixed"

    reply = await clova_client.chat_completion(
        system_prompt=SYSTEM_PROMPTS["general"],
        user_message=message,
        temperature=0.5,
    )
    return reply, "clova"
