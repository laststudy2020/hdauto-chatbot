"""재고 알림 수신자 자가등록 (설계 문서 7절).

초대 링크를 카톡으로 보내면 받는 사람이 눌러 카카오 동의만 하면
`alarm_recipients`에 행이 생긴다. 사람이 늘어도 개발자 개입이 필요 없다.

흐름:
    GET /api/admin/recipients/connect?key=<ADMIN_COMMAND_KEY>&name=<이름>
      → 1회용 논스 발급 후 카카오 동의 화면으로 302
    GET /api/admin/recipients/callback?code=&state=   (카카오가 호출)
      → 논스 소진 → 토큰 교환 → alarm_recipients upsert → 완료 안내 HTML

이 두 엔드포인트는 app/api/admin.py의 라우터와 달리 `require_admin_key`(X-Admin-Key
헤더)를 걸 수 없다. 카톡에서 링크를 눌러 들어오는 브라우저 요청이고 /callback은
카카오 서버가 리다이렉트로 호출하므로 어느 쪽도 헤더를 붙일 수 없다. 대신
/connect는 쿼리 `key`로, /callback은 서버가 발급한 1회용 논스로 게이트한다.
"""

import hmac
import logging
import secrets
import time
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.database import get_db
from app.db.models import AlarmRecipient

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/admin/recipients", tags=["admin"])

KAUTH_BASE = "https://kauth.kakao.com"

# "나에게 보내기"(memo/default/send)에 필요한 유일한 동의 항목.
KAKAO_SCOPE = "talk_message"

# 초대 링크에 ADMIN_COMMAND_KEY가 들어가므로 링크가 유출되면 임의의 사람이 수신자로
# 등록될 수 있다. 이를 막기 위해 링크를 1회용으로 만든다 — 발급 시 논스를 기록하고
# 콜백에서 소진시킨다. 프로세스 내 dict이므로 재시작 시 발급된 링크가 무효화되지만,
# 초대 직후 사용하는 흐름이라 실용상 문제가 없다.
_NONCE_TTL = 30 * 60
_nonces: dict[str, dict] = {}


def _issue_nonce(name: str) -> str:
    """1회용 state 값을 발급한다. 수신자 이름은 서버에 남기고 링크에 싣지 않는다."""
    _prune_nonces()
    state = secrets.token_urlsafe(32)
    _nonces[state] = {"name": name, "expires_at": time.time() + _NONCE_TTL}
    return state


def _consume_nonce(state: str) -> str | None:
    """state를 소진하고 수신자 이름을 돌려준다. 미발급/재사용/만료면 None.

    성공 여부와 무관하게 소진한다 — 실패 시 논스를 되돌리면 같은 링크로 무한히
    재시도할 수 있게 되어 1회용의 의미가 없어진다.
    """
    _prune_nonces()
    entry = _nonces.pop(state, None)
    if not entry:
        return None
    if entry["expires_at"] < time.time():
        return None
    return entry["name"]


def _prune_nonces() -> None:
    now = time.time()
    for state in [s for s, e in _nonces.items() if e["expires_at"] < now]:
        _nonces.pop(state, None)


async def _exchange_code_for_token(code: str) -> dict:
    """인가 코드를 access/refresh 토큰으로 교환한다."""
    async with httpx.AsyncClient(trust_env=False, timeout=10.0) as client:
        resp = await client.post(
            f"{KAUTH_BASE}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": settings.KAKAO_REST_API_KEY,
                "client_secret": settings.KAKAO_CLIENT_SECRET,
                "redirect_uri": settings.KAKAO_RECIPIENT_REDIRECT_URI,
                "code": code,
            },
        )
    if not resp.is_success:
        raise RuntimeError(f"카카오 토큰 교환 실패: {resp.text[:300]}")
    return resp.json()


async def _upsert_recipient(db: AsyncSession, name: str, token: dict) -> bool:
    """(name, channel='kakao') 기준 upsert. 신규 생성이면 True.

    이름이 같으면 같은 사람으로 본다. 카카오 사용자 id로 식별하는 편이 정확하지만
    그러려면 컬럼 추가 + /v2/user/me 호출이 필요하고, 수신자가 몇 명 수준이라
    이름 충돌 위험보다 스키마를 안 건드리는 이점이 크다.
    """
    existing = (await db.execute(
        select(AlarmRecipient).where(
            AlarmRecipient.name == name,
            AlarmRecipient.channel == "kakao",
        )
    )).scalars().first()

    created = existing is None
    row = existing or AlarmRecipient(name=name, channel="kakao")

    row.channel_token = token["refresh_token"]
    row.access_token = token.get("access_token")
    row.token_expires_in = token.get("expires_in")
    # admin_notify._get_valid_access_token()이 datetime.utcnow()와 비교하므로
    # 같은 기준(naive UTC)으로 저장해야 만료 판정이 어긋나지 않는다.
    row.token_obtained_at = datetime.utcnow()
    row.is_active = True

    if created:
        db.add(row)
    await db.commit()
    return created


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title></head>"
        "<body style=\"font-family:-apple-system,'Malgun Gothic',sans-serif;"
        "max-width:480px;margin:48px auto;padding:0 20px;line-height:1.7\">"
        f"{body}</body></html>"
    )


@router.get("/connect", summary="수신자 초대 링크 (카카오 동의 화면으로 이동)")
async def connect(
    key: str | None = Query(None, description="ADMIN_COMMAND_KEY"),
    name: str = Query(..., min_length=1, max_length=50, description="수신자 이름"),
):
    """초대 링크. 관리자가 이 URL을 만들어 카톡으로 보내면 받는 사람이 눌러 등록한다."""
    admin_key = settings.ADMIN_COMMAND_KEY
    # ADMIN_COMMAND_KEY가 없으면 비교 자체가 불가능하므로 무조건 401 (fail-closed).
    if not admin_key or not key or not hmac.compare_digest(key, admin_key):
        raise HTTPException(status_code=401, detail="관리자 인증 필요")

    redirect_uri = settings.KAKAO_RECIPIENT_REDIRECT_URI
    if not redirect_uri:
        # 빈 값으로 동의 화면에 보내면 카카오가 KOE006으로 거절해 원인 파악이 어렵다.
        raise HTTPException(
            status_code=503,
            detail="KAKAO_RECIPIENT_REDIRECT_URI가 설정되지 않았습니다. "
                   "카카오 개발자콘솔에 배포 도메인 콜백 URL을 등록하고 .env에 같은 값을 넣으세요.",
        )

    state = _issue_nonce(name)
    params = {
        "response_type": "code",
        "client_id": settings.KAKAO_REST_API_KEY,
        "redirect_uri": redirect_uri,
        "scope": KAKAO_SCOPE,
        "state": state,
    }
    url = f"{KAUTH_BASE}/oauth/authorize?{httpx.QueryParams(params)}"
    logger.info(f"[수신자등록] '{name}' 초대 링크 발급")
    return RedirectResponse(url, status_code=302)


@router.get("/callback", summary="카카오 동의 후 콜백 (수신자 등록)")
async def callback(
    state: str = Query(..., description="/connect가 발급한 1회용 논스"),
    code: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    name = _consume_nonce(state)
    if not name:
        raise HTTPException(
            status_code=400,
            detail="링크가 만료되었거나 이미 사용되었습니다. 새 초대 링크를 요청하세요.",
        )

    if error or not code:
        logger.warning(f"[수신자등록] '{name}' 동의 거절/실패: {error} {error_description}")
        return HTMLResponse(
            _page("등록 취소", "<h2>등록이 취소되었습니다</h2>"
                              "<p>다시 등록하려면 새 초대 링크를 요청해 주세요.</p>"),
            status_code=200,
        )

    try:
        token = await _exchange_code_for_token(code)
    except Exception as e:
        logger.error(f"[수신자등록] '{name}' 토큰 교환 실패: {e}")
        raise HTTPException(
            status_code=400,
            detail="카카오 인증에 실패했습니다. 새 초대 링크로 다시 시도해 주세요.",
        )

    if not token.get("refresh_token"):
        # refresh_token 없이 저장하면 access_token 만료 후 조용히 죽는다.
        logger.error(f"[수신자등록] '{name}' 응답에 refresh_token 없음")
        raise HTTPException(status_code=400, detail="카카오 응답에 refresh_token이 없습니다.")

    try:
        created = await _upsert_recipient(db, name, token)
    except Exception as e:
        logger.error(f"[수신자등록] '{name}' 저장 실패: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail="수신자 저장에 실패했습니다.")

    logger.info(f"[수신자등록] '{name}' {'신규 등록' if created else '토큰 갱신'} 완료")
    return HTMLResponse(_page(
        "등록 완료",
        f"<h2>✅ 등록 완료</h2>"
        f"<p><b>{name}</b> 님이 재고 알림 수신자로 "
        f"{'등록' if created else '재등록'}되었습니다.</p>"
        f"<p>앞으로 고객이 재고를 문의하면 카카오톡 "
        f"‘나와의 채팅’으로 알림이 전달됩니다.</p>"
        f"<p style='color:#888;font-size:14px'>이 창은 닫아도 됩니다.</p>",
    ))
