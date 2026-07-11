"""
register_hc_kfs_servo.py로 등록한 HC-KFS053/13/23/43(B) 데이터가 실제 챗봇 흐름
(chatbot._route)을 통해 고객에게 어떻게 응답되는지 검증.

- 실제 커밋된 데이터를 대상으로 조회만 하므로 이 스크립트 자체가 새 데이터를 넣지는
  않지만, 기존 test_*.py 패턴(rollback으로 흔적 안 남기기)을 그대로 유지하기 위해
  세션 끝에서 db.rollback()으로 마무리한다 (조회 도중 발생할 수 있는 부수 효과 방지).

확인 항목: 응답에 실제 치수 수치가 포함되는지, 면책 문구(disclaimer)가 붙는지,
감속기 정보가 없다는 안내("감속기 호환 정보 미등록")가 나오는지.
"""
import asyncio

from app.db.database import async_session
from app.core.intent import classify_intent
from app.api.chatbot import _route
from app.services.servo_spec_search import _DIMENSION_DISCLAIMER

TEST_CASES = [
    ("HC-KFS053 서보모터 감속기 부착하고 싶은데 사이즈 알려줘", "8mm", "25mm", "40mm"),
    ("HC-KFS13 서보모터 감속기 부착하고 싶은데 사이즈 알려줘", "8mm", "25mm", "40mm"),
    ("HC-KFS23 서보모터 감속기 부착하고 싶은데 사이즈 알려줘", "14mm", "30mm", "60mm"),
    ("HC-KFS43 서보모터 감속기 부착하고 싶은데 사이즈 알려줘", "14mm", "30mm", "60mm"),
]


async def main():
    async with async_session() as db:
        try:
            for message, shaft_dia, shaft_len, frame in TEST_CASES:
                print("=" * 70)
                print(f"질문: {message}")
                print("-" * 70)

                intent_result = classify_intent(message)
                print(f"[intent={intent_result.intent.value}, model={intent_result.model_name}]")

                reply, source = await _route(intent_result, message, db)
                print(f"[source={source}]\n")
                print(reply)
                print()

                assert _DIMENSION_DISCLAIMER in reply, "면책 문구 누락"
                assert "감속기 호환 정보 미등록" in reply, "'감속기 호환 정보 미등록' 안내 누락"
                assert shaft_dia in reply, f"축경 {shaft_dia} 누락"
                assert shaft_len in reply, f"축길이 {shaft_len} 누락"
                assert frame in reply, f"프레임 {frame} 누락"
                assert "치수 확인 불가" not in reply and "확인된 카탈로그 도면이" not in reply, (
                    "실제 치수가 있는데도 구식 '치수 없음' 안내가 같이 붙음 (모순)"
                )
                print("PASS\n")

            print("모든 테스트 통과.")
        finally:
            await db.rollback()


asyncio.run(main())
