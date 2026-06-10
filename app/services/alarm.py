from app.core.clova import clova_client, SYSTEM_PROMPTS


async def diagnose_alarm(alarm_code: str, model_name: str = None) -> str:
    context = f"알람코드: {alarm_code}"
    if model_name:
        context += f"\n제품 모델: {model_name}"

    response = await clova_client.chat_completion(
        system_prompt=SYSTEM_PROMPTS["alarm"],
        user_message=(
            f"[증상 정보]\n{context}\n\n"
            f"[질문]\n위 알람 코드의 원인과 해결 방법을 안내해 주세요.\n"
            f"(매뉴얼 DB 연동은 Phase 2에서 추가 예정입니다.)"
        ),
        temperature=0.2,
    )
    return response
