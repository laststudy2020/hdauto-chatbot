import re
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.replacement import register_replacement
from app.config import get_settings

settings = get_settings()
ADMIN_COMMAND_KEY = settings.ADMIN_COMMAND_KEY

_PATTERN = re.compile(
    r"^대체품등록\[(?P<key>[^\]]+)\]\s*:\s*(?P<old>[^|>]+?)\s*->\s*(?P<new>[^|]+?)\s*(?:\|\s*(?P<notes>.+))?$"
)


def is_admin_command(message: str) -> bool:
    return message.strip().startswith("대체품등록[")


async def handle_admin_command(message: str, db: AsyncSession) -> str:
    m = _PATTERN.match(message.strip())
    if not m:
        return (
            "명령어 형식이 올바르지 않습니다.\n"
            "형식: 대체품등록[키]: 기존모델 -> 신규모델 | 비고(선택)"
        )

    if not ADMIN_COMMAND_KEY:
        return "관리자 명령 기능이 설정되지 않았습니다. .env에 ADMIN_COMMAND_KEY를 추가해 주세요."

    if m.group("key") != ADMIN_COMMAND_KEY:
        return "인증에 실패했습니다."

    old_model = m.group("old").strip()
    new_model = m.group("new").strip()
    notes = m.group("notes")
    notes = notes.strip() if notes else None

    return await register_replacement(old_model, new_model, notes, db)