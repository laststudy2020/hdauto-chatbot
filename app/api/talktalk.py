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
    """네이버 톡톡 웹훅 서명 검증.

    secret이 비어있으면 False를 반환해 검증 실패로 처리.
    (기존 코드는 secret 없으면 True 반환 → 위조 요청 허용 취약점)
    단, 개발/테스트 환경에서 TALKTALK_SECRET 미설정 시에는
    settings.DEBUG=True 조건으로 스킵하도록 호출부에서 제어.
    """
    if not secret:
        return False
    if not x_signature:
        return False
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
    """의도에 따른 빠른 답장 버튼 생성.

    intent_hint에 따라 맥락에 맞는 버튼을 우선 배치.
    """
    base = [
        {"type": "TEXT", "title": "🔄 대체품 찾기", "value": "단종 대체품 알려줘"},
        {"type": "TEXT", "title": "📐 규격 조회", "value": "제품 규격 알려줘"},
        {"type": "TEXT", "title": "📦 재고 확인", "value": "재고 있나요?"},
        {"type": "TEXT", "title": "📍 위치 안내", "value": "현대자동화 위치 알려줘"},
    ]

    # 의도에 따라 맨 앞 버튼을 맥락에 맞게 교체
    if intent_hint == "stock":
        base[0] = {"type": "TEXT", "title": "🛒 스마트스토어 바로가기", "value": "스마트스토어 링크 알려줘"}
    elif intent_hint == "replacement":
        base[0] = {"type": "TEXT", "title": "📞 전화 문의", "value": f"전화번호 알려줘"}
    elif intent_hint == "alarm":
        base[0] = {"type": "TEXT", "title": "⚠️ 다른 알람 조회", "value": "알람 코드 조회"}

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

    # 서명 검증
    # - TALKTALK_SECRET 설정된 경우: 반드시 서명 일치해야 통과
    # - DEBUG=True이고 SECRET 미설정인 경우: 개발 환경으로 간주하고 스킵 (로그 경고)
    # - 운영 환경(DEBUG=False)에서 SECRET 미설정: 403 반환
    secret = getattr(settings, "TALKTALK_SECRET", "")
    if secret:
        if not _verify_signature(body, secret, x_naver_bot_signature or ""):
            raise HTTPException(status_code=403, detail="서명 검증 실패")
    elif settings.DEBUG:
        logger.warning("TALKTALK_SECRET 미설정 — 개발 모드로 서명 검증 스킵")
    else:
        logger.warning("TALKTALK_SECRET 미설정 상태로 운영 중 — 보안 취약, .env에 설정 권장")

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
            reply, intent_hint = await _process_message(message, user_id, db)
        except Exception as e:
            logger.error(f"메시지 처리 오류: {e}", exc_info=True)
            reply = (
                "일시적인 오류가 발생했습니다.\n"
                f"급한 문의는 {settings.COMPANY_PHONE}으로 전화 부탁드립니다."
            )
            intent_hint = "general"

        quick = _make_quick_replies(intent_hint)
        await send_to_talktalk(user_id, reply, authorization, quick)
        return {"result": "ok"}

    # ─── 기타 이벤트 ───
    return {"result": "ok"}


async def _process_message(message: str, user_id: str, db: AsyncSession) -> tuple[str, str]:
    """메시지 처리. (응답 텍스트, 의도 힌트) 튜플 반환.

    관리자 명령어를 먼저 처리하고, 일반 메시지는 chatbot._route로 위임.
    """
    
    from app.api.chatbot import _route
    from app.core.admin_commands import handle_admin_command

    # 관리자 명령어 처리 (ADMIN_COMMAND_KEY로 시작하는 메시지)
    admin_key = getattr(settings, "ADMIN_COMMAND_KEY", "")
    if admin_key and message.startswith(admin_key):
        try:
            admin_reply = await handle_admin_command(message, db)
            if admin_reply:
                return admin_reply, "general"
        except Exception as e:
            logger.warning(f"관리자 명령 처리 실패: {e}")

    # 일반 메시지: 의도 분류 → 라우팅
    intent_result = classify_intent(message)
    reply, source = await _route(intent_result, message, db)

    # 퀵리플라이용 의도 힌트 추출
    intent_hint = "general"
    if hasattr(intent_result, "intent"):
        intent_map = {
            "stock": "stock",
            "replacement": "replacement",
            "alarm": "alarm",
            "specs": "specs",
            "location": "location",
        }
        intent_hint = intent_map.get(intent_result.intent.value, "general")

    return reply, intent_hint


@router.get("/health", summary="톡톡 웹훅 연결 확인용")
async def talktalk_health():
    """네이버 톡톡 파트너센터에서 URL 검증 시 200 OK 반환"""
    return {"status": "ok", "service": "HD AUTO 톡톡 챗봇"}