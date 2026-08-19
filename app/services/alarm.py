from sqlalchemy import select, or_, distinct
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import AlarmCode
from app.core.clova import clova_client, SYSTEM_PROMPTS


def _series_key(alarm) -> str:
    """'LSLV-G100' → 'G100'. 질문 문자열과 대조할 짧은 식별자."""
    return (alarm.product_series or "").upper().rsplit("-", 1)[-1]


async def _known_series_keys(db: AsyncSession) -> set[str]:
    """DB에 적재된 전 시리즈의 식별자 집합 ({'G100','H100','S100','IG5A'} 등).

    검색 결과 행에서 뽑으면 안 된다. 질문한 시리즈에 그 코드가 아예 없을 때
    후보 행에는 형제 시리즈만 남아 있어서, 정작 특정해야 할 시리즈를 '언급 안 됨'으로
    오판하고 형제 행을 그대로 넘기게 된다 — 막으려던 경로가 바로 그것이다.
    """
    rows = (await db.execute(select(distinct(AlarmCode.product_series)))).scalars().all()
    return {k for s in rows if len(k := (s or "").upper().rsplit("-", 1)[-1]) >= 3}


def _select_series(alarms: list, hint: str, known: set[str]) -> tuple[list, bool]:
    """질문에 나온 시리즈만 남긴다. Returns: (사용할 행, 시리즈를 특정했는가)

    제조사가 계열마다 같은 표기를 재사용한다(SV-iG5A의 OLt, LSLV-S100의 OLT).
    예전엔 정렬만 하고 상위 5건을 잘라 넘겼는데, 정렬은 형제 시리즈 행을 프롬프트
    밖으로 밀어내지 못한다. G100은 조치(solution)가 35건 전부 비어 있어서 LLM이
    같이 실린 H100 조치로 빈칸을 메웠다(2026-08-10 프로덕션 재현). 그래서 필터다.

    시리즈를 특정했는데 그 시리즈에 해당 코드가 없으면 **빈 리스트**를 돌려준다.
    형제 시리즈 행으로 대신 답하면 매뉴얼에 존재하지도 않는 코드를 실재하는 것처럼
    설명하게 된다 — "없다"고 답하는 편이 정확하다.
    """
    up = hint.upper()
    mentioned = {k for k in known if k in up}
    if not mentioned:
        return alarms, False
    return [a for a in alarms if _series_key(a) in mentioned], True


async def diagnose_alarm(
    alarm_code: str = None,
    model_name: str = None,
    user_message: str = "",
    db: AsyncSession = None
) -> tuple[str, bool]:
    """알람코드 진단 - DB 매뉴얼 우선, 없으면 일반 지식
    Returns: (reply, matched_in_db)
    """

    db_context = ""
    matched = False
    absent_note = ""

    if db and (alarm_code or model_name):
        conditions = []
        if alarm_code:
            conditions.append(AlarmCode.alarm_code.ilike(f"%{alarm_code}%"))
        if model_name:
            conditions.append(AlarmCode.product_series.ilike(f"%{model_name}%"))

        if conditions:
            # 시리즈 필터는 파이썬쪽에서 하므로(형명 'LSLV0022G100-2'는 시리즈 문자열
            # 'LSLV-G100'과 직접 매칭되지 않는다) 후보를 넉넉히 받아둔다.
            stmt = select(AlarmCode).where(or_(*conditions)).limit(50)
            result = await db.execute(stmt)
            candidates = result.scalars().all()
            alarms, series_identified = _select_series(
                candidates,
                f"{user_message} {model_name or ''}",
                await _known_series_keys(db),
            )
            alarms = alarms[:5]

            if candidates and series_identified and not alarms:
                # 시리즈는 알겠는데 그 매뉴얼에 이 코드가 없는 경우.
                absent_note = (
                    f"\n\n해당 시리즈의 공식 매뉴얼 알람표에는 '{alarm_code}' 코드가 "
                    f"없습니다. 다른 시리즈의 알람표를 끌어다 설명하지 말고, 코드 표기를 "
                    f"다시 확인하도록 안내해 주세요."
                )

            if alarms:
                db_context = "\n".join([
                    f"[{a.alarm_code}] {a.alarm_name} — {a.manufacturer} {a.product_series}\n"
                    f"원인: {a.cause or '(매뉴얼에 원인 설명 없음)'}\n"
                    # 빈 칸으로 두면 LLM이 일반 지식으로 조치를 지어낸다.
                    f"해결: {a.solution or '(매뉴얼 조치표에 항목 없음 — 지어내지 말 것)'}\n"
                    f"출처: {a.manual_filename or '매뉴얼'} p.{a.manual_page}"
                    for a in alarms
                ])
                matched = True

                # 시리즈를 특정하지 못해 여러 계열이 같이 실린 경우. 행마다 시리즈가
                # 적혀 있어도 LLM은 비어 있는 조치를 옆 시리즈 값으로 메운다.
                if len({_series_key(a) for a in alarms}) > 1:
                    db_context += (
                        "\n\n※ 위 결과는 서로 다른 제품 시리즈의 알람표입니다. "
                        "시리즈끼리 원인·조치·설정값을 절대 섞지 말고, 시리즈별로 "
                        "구분해서 답하세요. 어느 제품인지 되물어 확인하세요."
                    )

    if db_context:
        reply = await clova_client.chat_completion(
            system_prompt=SYSTEM_PROMPTS["alarm"],
            user_message=(
                f"[공식 매뉴얼 검색 결과]\n{db_context}\n\n"
                f"[질문]\n{user_message or alarm_code}"
            ),
            temperature=0.1,
        )
    else:
        reply = await clova_client.chat_completion(
            system_prompt=SYSTEM_PROMPTS["alarm"],
            user_message=(
                f"[알람 정보]\n"
                f"코드: {alarm_code or '미확인'}\n"
                f"제품: {model_name or '미확인'}\n\n"
                f"[질문]\n{user_message}\n\n"
                f"매뉴얼 DB에 해당 정보가 없습니다.{absent_note} "
                f"FA 부품 일반 지식으로 안내하고, "
                f"정확한 해결을 위해 공식 매뉴얼 확인을 권고해 주세요."
            ),
            temperature=0.2,
        )

    return reply, matched