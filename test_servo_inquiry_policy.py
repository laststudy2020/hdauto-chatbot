"""
서보모터 문의 응답 정책(find_drives_compatible_with_motor / find_reducer_compat 통합) 검증.

3가지 케이스:
- A: 치수 + 감속기(자동매칭) 모두 있음 → HC-KFS43 (실제 등록 데이터, 챗봇 흐름 전체)
- B: 치수는 있으나 축경(shaft_diameter_mm) 미등록이라 감속기 판단 근거가 없음
     → 실제 등록 데이터로는 재현 불가(등록된 4개 모터 전부 축경 있음)이므로
     _format_motor_spec_block을 합성 데이터로 직접 호출해 검증.
- C: 치수/감속기 둘 다 미등록 → HC-KFS73 (실제 데이터: MR-J2S-70A/B의
     compatible_motors에는 있지만 motor_specs에는 없음)

공통으로 확인:
- "정격 출력전류: -A" 같은 자리채움 대신 데이터 없는 필드는 아예 생략되는지
  (MR-J2S 계열은 rated_output_current_a를 애초에 등록한 적이 없음)
- 마감 문구(_DIMENSION_DISCLAIMER)가 치수 등록/미등록 무관하게 정확히 1회만,
  전화번호(010-3861-2030)를 포함해서 나오는지
"""
import asyncio

from app.db.database import async_session
from app.core.intent import classify_intent
from app.api.chatbot import _route
from app.services.servo_spec_search import (
    _DIMENSION_DISCLAIMER,
    _format_motor_spec_block,
    _all_reducer_rows,
)

PHONE = "010-3861-2030"


async def case_a(db):
    print("=" * 70)
    print("[케이스 A] 치수+감속기 모두 등록 — HC-KFS43")
    print("-" * 70)

    message = "HC-KFS43 서보모터 감속기 부착하고 싶은데 사이즈 알려줘"
    intent_result = classify_intent(message)
    reply, source = await _route(intent_result, message, db)
    print(reply)

    assert reply.count("호환 가능한 서보드라이브 2종") == 1, "A/B 계열 대표 2종만 나와야 함"
    assert "정격 출력전류" not in reply, (
        "MR-J2S는 정격 출력전류 데이터가 없으므로 '-A' 자리채움 없이 줄 자체가 생략돼야 함"
    )
    assert "축경 14mm" in reply and "전장 124.5" in reply, "치수 수치 누락"
    assert "AB060" in reply and "🔩 정확한 모터 장착 어댑터" in reply, "감속기 자동매칭 결과 누락"
    assert "감속기 호환 정보 미등록" not in reply, "축경이 있는데 구식 미등록 문구가 나옴"
    assert reply.count(PHONE) == 1, f"문의 전화번호가 정확히 1회 나와야 함 (실제 {reply.count(PHONE)}회)"
    assert reply.count(_DIMENSION_DISCLAIMER.strip()) == 1, "치수 disclaimer가 정확히 1회 나와야 함"
    print("PASS\n")


async def case_b():
    print("=" * 70)
    print("[케이스 B] 치수는 있으나 축경 미등록 → 감속기 섹션 생략 (합성 유닛 테스트)")
    print("-" * 70)

    reducer_rows = []  # 이 케이스는 축경이 없어 조회 자체가 안 일어나므로 빈 리스트로 충분
    motor_data = {
        "power_w": 100, "rated_torque_nm": 0.3, "max_torque_nm": 0.9,
        "rated_speed_rpm": 3000, "max_speed_rpm": 4500,
        "dimensions": {"frame_size_mm": 40, "body_size_mm": 42, "overall_length_mm": 90},
        "reducers": [],
    }
    block = _format_motor_spec_block("TEST-CASE-B-NO-BORE", motor_data, reducer_rows)
    print(block)

    assert "전기사양" in block, "치수 등록 여부와 무관하게 기본 구동사양은 항상 나와야 함"
    assert "치수:" in block and "플랜지 프레임 □40mm" in block, "등록된 치수는 나와야 함"
    assert "축경" not in block, "축경 자체가 없으므로 나오면 안 됨"
    assert "결합 가능 감속기" not in block, "감속기 판단 근거(축경)가 없으면 섹션 자체가 생략돼야 함"
    assert "미등록" not in block, "자리채움 문구도 나오면 안 됨"
    print("PASS\n")


async def case_c(db):
    print("=" * 70)
    print("[케이스 C] 치수/감속기 둘 다 미등록 — HC-KFS73")
    print("-" * 70)

    message = "HC-KFS73 서보모터 감속기 부착하고 싶은데 사이즈 알려줘"
    intent_result = classify_intent(message)
    reply, source = await _route(intent_result, message, db)
    print(reply)

    assert "호환 가능한 서보드라이브" in reply, "드라이브 목록은 나와야 함(compatible_motors에는 등록됨)"
    assert "결합사양:" not in reply, "motor_specs 자체가 없으므로 결합사양 섹션이 없어야 함"
    assert "카탈로그 도면이" in reply, "치수 미등록 설명 문구가 나와야 함"
    assert reply.count(PHONE) == 1, f"문의 전화번호가 정확히 1회 나와야 함 (실제 {reply.count(PHONE)}회)"
    assert reply.count(_DIMENSION_DISCLAIMER.strip()) == 1, "마감 disclaimer가 정확히 1회 나와야 함"
    print("PASS\n")


async def main():
    async with async_session() as db:
        try:
            await case_a(db)
            await case_b()
            await case_c(db)
            print("모든 정책 테스트(A/B/C) 통과.")
        finally:
            await db.rollback()


asyncio.run(main())
