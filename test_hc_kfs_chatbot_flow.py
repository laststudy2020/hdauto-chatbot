"""
register_hc_kfs_servo.py로 등록한 HC-KFS053/13/23/43(B) 데이터 + register_apex_reducer.py로
등록한 APEX AB/ABR 감속기 카탈로그가 실제 챗봇 흐름(chatbot._route)을 통해 고객에게
어떻게 응답되는지 검증.

- 실제 커밋된 데이터를 대상으로 조회만 하므로 이 스크립트 자체가 새 데이터를 넣지는
  않지만, 기존 test_*.py 패턴(rollback으로 흔적 안 남기기)을 그대로 유지하기 위해
  세션 끝에서 db.rollback()으로 마무리한다 (조회 도중 발생할 수 있는 부수 효과 방지).

확인 항목:
1~4) HC-KFS053/13/23/43 각각: 응답에 실제 치수 수치가 포함되는지, 치수 면책 문구가
     붙는지, 감속기 자동매칭 결과(+ 어댑터 확인 문구)가 나오는지 — 이 4개 모터는
     전부 실제로 APEX 카탈로그와 매칭되므로(8mm -> 최소 등급 6건: AB042/AB060(2단)/
     ABR042/ABR060(2단), 14mm -> 최소 등급 5건: AB060/AB060A/AB090(2단)/ABR060/
     ABR090(2단)) "감속기 호환 정보 미등록"이 더는 나오면 안 된다.
5) [단위 테스트] _match_reducers_by_bore를 존재하지 않는 가상 축경(100mm, AB220
   최대 입력홀 55mm보다 큼)으로 직접 호출해 빈 리스트를 반환하는지, 그리고
   _format_motor_spec_block에 그 축경을 가진 가상 모터를 넣었을 때 "AB/ABR 라인업 내
   호환 모델 없음" 문구가 정확히 나오는지 확인 — 실제 등록 모터로는 이 분기에
   도달할 수 없으므로 함수를 직접 호출해서만 검증 가능하다.
"""
import asyncio

from app.db.database import async_session
from app.core.intent import classify_intent
from app.api.chatbot import _route
from app.services.servo_spec_search import (
    _DIMENSION_DISCLAIMER,
    _REDUCER_ADAPTER_DISCLAIMER,
    _all_reducer_rows,
    _match_reducers_by_bore,
    _format_motor_spec_block,
)

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

                assert _DIMENSION_DISCLAIMER in reply, "치수 면책 문구 누락"
                assert shaft_dia in reply, f"축경 {shaft_dia} 누락"
                assert shaft_len in reply, f"축길이 {shaft_len} 누락"
                assert frame in reply, f"프레임 {frame} 누락"
                assert "치수 확인 불가" not in reply and "확인된 카탈로그 도면이" not in reply, (
                    "실제 치수가 있는데도 구식 '치수 없음' 안내가 같이 붙음 (모순)"
                )
                assert "감속기 호환 정보 미등록" not in reply, (
                    "축경이 있는 모터인데 구식 '미등록' 문구가 나옴 (자동매칭 미적용)"
                )
                assert "AB/ABR 라인업 내 호환 모델 없음" not in reply, (
                    "실제로는 매칭되는 모델이 있어야 하는데 '매칭 없음'이 나옴"
                )
                assert _REDUCER_ADAPTER_DISCLAIMER.strip() in reply, "감속기 어댑터(C1~C10) 확인 문구 누락"
                print("PASS\n")

            print("모든 챗봇 흐름 테스트(1~4) 통과.")

            # 5) 단위 테스트: 존재하지 않는 가상 축경(100mm)으로 '매칭 없음' 분기 검증.
            # 등록된 4개 모터는 전부 실제 매칭되므로 챗봇 흐름으로는 이 분기에 도달 못 함.
            print("=" * 70)
            print("[단위 테스트] 가상 축경 100mm — 매칭 없음 분기")
            print("-" * 70)

            reducer_rows = await _all_reducer_rows(db)
            no_matches = _match_reducers_by_bore(100.0, reducer_rows)
            assert no_matches == [], f"100mm은 어느 모델에도 안 맞아야 하는데 매칭됨: {no_matches}"
            print("_match_reducers_by_bore(100.0, ...) == [] : PASS")

            fake_motor_data = {
                "dimensions": {"shaft_diameter_mm": 100.0},
                "reducers": [],
            }
            block = _format_motor_spec_block("TEST-VIRTUAL-100MM", fake_motor_data, reducer_rows)
            print(block)
            assert "AB/ABR 라인업 내 호환 모델 없음" in block, "매칭 없음 문구 누락"
            assert "다른 감속기 시리즈 또는 커스텀 확인 필요" in block, "매칭 없음 안내 문구 불완전"
            assert _REDUCER_ADAPTER_DISCLAIMER.strip() not in block, (
                "매칭이 없는데 어댑터 확인 문구가 붙음 (매칭 있을 때만 붙어야 함)"
            )
            print("PASS\n")

            print("모든 테스트 통과.")
        finally:
            await db.rollback()


asyncio.run(main())
