import hmac
import hashlib
import json
import httpx
import logging
from fastapi import APIRouter, Request, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.core.intent import classify_intent
from app.services.replacement import find_replacement
from app.services.specs import lookup_specs
from app.services.location import get_location_response
from app.services.inventory import get_inventory_status
from app.core.clova import clova_client, SYSTEM_PROMPTS
from app.core.intent import Intent
from app.config import get_settings
from fastapi import Depends

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/talktalk", tags=["talktalk"])
settings = get_settings()

TALKTALK_SEND_URL = "https://gw.talk.naver.com/chatbot/v1/event"


def _verify_signature(body: bytes, secret: str, x_signature: str) -> bool:
    """네이버 톡톡 웹훅 서명 검증"""
    if not secret or not x_signature:
        return True
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, x_signature.replace("sha256=", ""))


async def send_to_talktalk(
    user_id: str,
    text: str,
    authorization: str,
    quick_replies: list[dict] | None = None,
):
    """네이버 톡톡 보내기 API 호출"""
    payload = {
        "event": "send",
        "user": user_id,
        "textContent": {"text": text},
    }
    if quick_replies:
        payload["quickReply"] = {"buttonList": quick_replies}

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Authorization": authorization,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(TALKTALK_SEND_URL, headers=headers, json=payload)
        logger.info(f"톡톡 응답: {resp.status_code} {resp.text[:100]}")
        return resp.status_code


def _make_quick_replies(intent_hint: str) -> list[dict]:
    """의도에 따른 빠른 답장 버튼 생성"""
    base = [
        {"type": "TEXT", "title": "대체품 찾기", "value": "단종 대체품 알려줘"},
        {"type": "TEXT", "title": "규격 조회", "value": "제품 규격 알려줘"},
        {"type": "TEXT", "title": "재고 확인", "value": "재고 있나요?"},
        {"type": "TEXT", "title": "위치 안내", "value": "현대자동화 위치 알려줘"},
    ]
    return base[:4]


@router.post("/webhook", summary="네이버 톡톡 웹훅 수신")
async def talktalk_webhook(
    request: Request,
    x_naver_bot_signature: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """
    네이버 톡톡 파트너센터 > 개발자도구 > 챗봇API > Webhook URL에 등록
    예: https://your-domain.com/api/talktalk/webhook
    """
    body = await request.body()

    # 서명 검증 (TALKTALK_SECRET 설정 시 활성화)
    secret = getattr(settings, "TALKTALK_SECRET", "")
    if secret and not _verify_signature(body, secret, x_naver_bot_signature or ""):
        raise HTTPException(status_code=403, detail="서명 검증 실패")

    try:
        event = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="잘못된 JSON")

    authorization = getattr(settings, "TALKTALK_AUTHORIZATION", "")
    event_type = event.get("event", "")
    user_id = event.get("user", "")

    logger.info(f"톡톡 이벤트: {event_type} / 사용자: {user_id}")

    # ─── open: 대화 시작 ───
    if event_type == "open":
        welcome = (
            "안녕하세요! 현대자동화 AI 부품 도우미입니다 🤖\n\n"
            "FA 산업 자동화 부품 전문 24시간 자동응답 서비스입니다.\n\n"
            "아래 버튼을 누르거나 모델명을 바로 입력해 주세요!\n\n"
            "🔄 단종 대체품 찾기 → 예) FX3U-32MT 대체품\n"
            "📐 규격/사이즈 조회 → 예) FX5U-32MT 사이즈\n"
            "⚠️ 고장 알람 진단 → 예) OC1 알람 원인\n"
            "📦 재고 확인 → 예) FX5U-32MT 재고 있나요?\n"
            "📍 위치 안내 → 예) 현대자동화 위치 알려줘"
        )
        quick = _make_quick_replies("open")
        await send_to_talktalk(user_id, welcome, authorization, quick)
        return {"result": "ok"}

    # ─── leave: 대화 종료 ───
    if event_type == "leave":
        logger.info(f"사용자 {user_id} 대화 종료")
        return {"result": "ok"}

    # ─── send: 메시지 수신 ───
    if event_type == "send":
        text_content = event.get("textContent", {})
        message = text_content.get("text", "").strip()

        if not message:
            return {"result": "ok"}

        logger.info(f"[톡톡] {user_id}: {message}")

        try:
            reply = await _process_message(message, user_id, db)
        except Exception as e:
            logger.error(f"메시지 처리 오류: {e}", exc_info=True)
            reply = (
                "일시적인 오류가 발생했습니다.\n"
                f"급한 문의는 {settings.COMPANY_PHONE}으로 전화 부탁드립니다."
            )

        quick = _make_quick_replies("general")
        await send_to_talktalk(user_id, reply, authorization, quick)
        return {"result": "ok"}

    # ─── 기타 이벤트 ───
    return {"result": "ok"}


async def _process_message(message: str, user_id: str, db: AsyncSession) -> str:
    """메시지 처리 - chatbot.py 라우팅 로직과 동일"""
    intent_result = classify_intent(message)
    intent = intent_result.intent
    model = intent_result.model_name

    if intent == Intent.REPLACEMENT:
        if model:
            return await find_replacement(model, db)
        return "어떤 모델의 대체품을 찾고 계신가요?\n모델명을 입력해 주세요.\n예) FX3U-32MT 대체품"

    if intent == Intent.SPECS:
        if model:
            return await lookup_specs(model, db)
        return "어떤 제품의 규격을 확인하고 싶으신가요?\n모델명을 입력해 주세요.\n예) FX5U-32MT 사이즈"

    if intent == Intent.ALARM:
        alarm = intent_result.alarm_code
        context = f"알람코드: {alarm}" if alarm else ""
        if model:
            context += f"\n제품: {model}"
        return await clova_client.chat_completion(
            system_prompt=SYSTEM_PROMPTS["alarm"],
            user_message=f"[증상 정보]\n{context or message}\n\n[질문]\n{message}",
            temperature=0.2,
        )

    if intent == Intent.LOCATION:
        return get_location_response()

    if intent == Intent.STOCK:
        if model:
            return await get_inventory_status(model, db)
        return "어떤 제품의 재고를 확인하고 싶으신가요?\n모델명을 입력해 주세요."

    return await clova_client.chat_completion(
        system_prompt=SYSTEM_PROMPTS["general"],
        user_message=message,
        temperature=0.5,
    )


@router.get("/health", summary="톡톡 웹훅 연결 확인용")
async def talktalk_health():
    """네이버 톡톡 파트너센터에서 URL 검증 시 200 OK 반환"""
    return {"status": "ok", "service": "HD AUTO 톡톡 챗봇"}
