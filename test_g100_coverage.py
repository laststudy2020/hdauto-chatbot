"""G100 알람 35건이 고객 문장으로 실제 도달되는지 전수 확인.

DB에 행이 있어도 의도 분류가 알람으로 못 잡으면 그 행은 영원히 안 쓰인다.
적재된 명칭을 그대로 써서 고객이 쓸 법한 문장을 만들고, (1) 알람 의도로
잡히는가 (2) diagnose_alarm이 DB 행에 매칭되는가를 본다. CLOVA는 스텁.
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select

from app.core import clova as clova_mod
from app.core.intent import Intent, classify_intent
from app.db.database import async_session
from app.db.models import AlarmCode
from app.services.alarm import diagnose_alarm

_real_chat = clova_mod.clova_client.chat_completion
_captured: list[str] = []


async def _stub_chat(system_prompt: str, user_message: str, **kwargs) -> str:
    _captured.append(user_message)
    return "[STUB]"


async def main() -> None:
    clova_mod.clova_client.chat_completion = _stub_chat

    async with async_session() as db:
        rows = (await db.execute(
            select(AlarmCode).where(AlarmCode.product_series == "LSLV-G100")
            .order_by(AlarmCode.alarm_code)
        )).scalars().all()

        unreachable, no_solution, ok = [], [], []
        for r in rows:
            msg = f"G100 인버터에 {r.alarm_code} 트립이 떴습니다"
            ir = classify_intent(msg)
            reachable = ir.intent == Intent.ALARM

            matched = False
            if reachable:
                _captured.clear()
                _, matched = await diagnose_alarm(
                    alarm_code=ir.alarm_code, model_name=ir.model_name,
                    user_message=msg, db=db)

            has_sol = bool(r.solution and r.solution.strip())
            if not has_sol:
                no_solution.append(r.alarm_code)

            if not (reachable and matched):
                unreachable.append(
                    f"{r.alarm_code:22} intent={ir.intent.value:11} "
                    f"code={ir.alarm_code!r} matched={matched}")
            else:
                ok.append(r.alarm_code)

        print(f"=== 도달 실패 {len(unreachable)}/{len(rows)}건 ===")
        for line in unreachable:
            print(f"  {line}")
        print(f"\n=== 도달 성공 {len(ok)}건 ===")
        print("  " + ", ".join(ok))
        print(f"\n=== 조치(solution) 비어 있는 항목 {len(no_solution)}/{len(rows)}건 ===")
        print("  " + ", ".join(no_solution))

        # 비교군: S100/H100은 조치가 채워져 있는가
        for series in ("LSLV-S100", "LSLV-H100"):
            srows = (await db.execute(
                select(AlarmCode).where(AlarmCode.product_series == series)
            )).scalars().all()
            empty = [r.alarm_code for r in srows
                     if not (r.solution and r.solution.strip())]
            print(f"\n{series}: 조치 비어 있음 {len(empty)}/{len(srows)}건")

    clova_mod.clova_client.chat_completion = _real_chat


if __name__ == "__main__":
    asyncio.run(main())
