import hmac
from fastapi import Header, HTTPException
from app.config import get_settings

settings = get_settings()


async def require_admin_key(x_admin_key: str | None = Header(None)):
    """관리자 전용 API(재고 수정/상품 등록 등)에 X-Admin-Key 헤더 인증을 요구한다.

    ADMIN_COMMAND_KEY가 .env에 설정되지 않은 경우 키 비교 자체가 불가능하므로
    무조건 401 — 인증 없이 공개하던 기존 동작(코드리뷰 H2)을 막기 위한 fail-closed.
    """
    key = settings.ADMIN_COMMAND_KEY
    if not key or not x_admin_key or not hmac.compare_digest(x_admin_key, key):
        raise HTTPException(status_code=401, detail="관리자 인증 필요 (X-Admin-Key 헤더)")
