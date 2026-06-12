from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import AlarmCode
from app.core.clova import clova_client, SYSTEM_PROMPTS


async def diagnose_alarm(
    alarm_code: str = None,
    model_name: str = None,
    user_message: str = "",
    db: AsyncSession = None
) -> str:
    """알람코드 진단 - DB 매뉴얼 우선, 없으면 일반 지식"""

    db_context = ""
    source = "general"

    if db and (alarm_code or model_name):
        # DB에서 알람코드 검색
        conditions = []
        if alarm_code:
            conditions.append(AlarmCode.alarm_code.ilike(f"%{alarm_code}%"))
        if model_name:
            conditions.append(AlarmCode.product_series.ilike(f"%{model_name}%"))

        if conditions:
            stmt = select(AlarmCode).where(or_(*conditions)).limit(5)
            result = await db.execute(stmt)
            alarms = result.scalars().all()

            if alarms:
                db_context = "\n".join([
                    f"[{a.alarm_code}] {a.alarm_name}\n"
                    f"원인: {a.cause}\n"
                    f"해결: {a.solution}\n"
                    f"출처: {a.manual_filename or '매뉴얼'} p.{a.manual_page}"
                    for a in alarms
                ])
                source = "manual_db"

    if db_context:
        # 매뉴얼 기반 답변
        reply = await clova_client.chat_completion(
            system_prompt=SYSTEM_PROMPTS["alarm"],
            user_message=(
                f"[공식 매뉴얼 검색 결과]\n{db_context}\n\n"
                f"[질문]\n{user_message or alarm_code}"
            ),
            temperature=0.1,
        )
    else:
        # 일반 지식 기반 답변
        reply = await clova_client.chat_completion(
            system_prompt=SYSTEM_PROMPTS["alarm"],
            user_message=(
                f"[알람 정보]\n"
                f"코드: {alarm_code or '미확인'}\n"
                f"제품: {model_name or '미확인'}\n\n"
                f"[질문]\n{user_message}\n\n"
                f"매뉴얼 DB에 해당 정보가 없습니다. "
                f"FA 부품 일반 지식으로 안내하고, "
                f"정확한 해결을 위해 공식 매뉴얼 확인을 권고해 주세요."
            ),
            temperature=0.2,
        )

    return reply
