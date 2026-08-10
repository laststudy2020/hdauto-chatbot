"""G100 적재분이 실제 고객 응답으로 제대로 나가는지 확인.

test_ls_manual_ingest.py가 "DB에 행이 있는가"를 본다면, 이 스크립트는 그 행이
챗봇 답변 문장까지 살아서 나오는가를 본다. 라우터(chatbot._route)를 그대로
태우므로 의도 분류 → 서비스 → 프롬프트까지 실제 경로와 동일하다.

  python test_g100_reply.py           # CLOVA 스텁 (무료, 프롬프트 내용만 검사)
  python test_g100_reply.py --live    # 실제 CLOVA 호출 — 최종 문장까지 확인

재고 문의는 넣지 않았다. 관리자에게 카카오 알림이 나간다.
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.core import clova as clova_mod
from app.core.intent import classify_intent
from app.db.database import async_session

LIVE = "--live" in sys.argv

_captured: list[tuple[str, str]] = []
_real_chat = clova_mod.clova_client.chat_completion


async def _stub_chat(system_prompt: str, user_message: str, **kwargs) -> str:
    _captured.append((system_prompt, user_message))
    return "[STUB] LLM 응답"


async def _live_chat(system_prompt: str, user_message: str, **kwargs) -> str:
    _captured.append((system_prompt, user_message))
    return await _real_chat(system_prompt=system_prompt, user_message=user_message,
                            **kwargs)


# (질문, 프롬프트에 실려야 할 조각)
CASES = [
    ("G100 인버터에 Over Current1 뜹니다", ["Over Current1", "200%"]),
    ("LSLV0022G100-2 Over Voltage 트립 떴어요", ["Over Voltage"]),
    ("G100에 Ground 트립이 떠요", ["Ground"]),
    ("G100 No Motor Trip 발생했습니다", ["No Motor Trip"]),
    ("LSLV0022G100-2 사양 알려주세요", ["2.2", "G100"]),
    ("LSLV0040G100-4 외형 치수랑 중량 알려주세요", ["G100"]),
]


async def main() -> None:
    clova_mod.clova_client.chat_completion = _live_chat if LIVE else _stub_chat
    from app.api.chatbot import _route

    async with async_session() as db:
        for message, fragments in CASES:
            _captured.clear()
            print(f"\n{'=' * 74}\nQ: {message}")
            r = classify_intent(message)
            print(f"  intent={r.intent.value} model={r.model_name} "
                  f"alarm={r.alarm_code!r}")
            reply, source = await _route(r, message, db)
            print(f"  source={source}")

            ctx = _captured[-1][1] if _captured else ""
            missing = [f for f in fragments if f not in ctx]
            print(f"  프롬프트 조각 {fragments} → "
                  f"{'OK' if not missing else f'누락 {missing}'}")
            print(f"  --- LLM에 넘어간 컨텍스트 ---\n{ctx[:900]}")
            print(f"  --- 최종 답변 ---\n{reply[:900]}")

    clova_mod.clova_client.chat_completion = _real_chat


if __name__ == "__main__":
    asyncio.run(main())
