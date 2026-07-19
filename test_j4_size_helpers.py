"""
J4 사이즈 기준표 + 브레이크 접미사/모터형명 판별 헬퍼 단위 테스트 (순수 함수, DB 불필요).
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.services.servo_spec_search import (
    J4_SIZE_TABLE, _J4_SIZE_BY_CAPACITY, _is_motor_model_name, _split_brake_suffix,
)
from register_j4_motor_sizes import J4_SIZE_ROWS

failures = []


def check(label, actual, expected):
    status = "PASS" if actual == expected else "FAIL"
    if status == "FAIL":
        failures.append(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"[{status}] {label}: {actual!r}")


# ── J4_SIZE_TABLE 개수/조회 ──
check("J4_SIZE_TABLE 12행", len(J4_SIZE_TABLE), 12)
check("400W -> 플랜지 60mm", _J4_SIZE_BY_CAPACITY[400]["frame_mm"], 60)
check("400W -> 드라이브A", _J4_SIZE_BY_CAPACITY[400]["drive_a"], "MR-J4-40A")
check("400W -> 드라이브B", _J4_SIZE_BY_CAPACITY[400]["drive_b"], "MR-J4-40B")
check("750W -> HG-KR73", _J4_SIZE_BY_CAPACITY[750]["hg_kr"], "HG-KR73")
check("1500W/2000W 둘 다 드라이브A=MR-J4-200A",
      (_J4_SIZE_BY_CAPACITY[1500]["drive_a"], _J4_SIZE_BY_CAPACITY[2000]["drive_a"]),
      ("MR-J4-200A", "MR-J4-200A"))
check("미등록 용량(850W)은 없음", 850 in _J4_SIZE_BY_CAPACITY, False)

# ── J4_SIZE_TABLE(폴백 유추 근거)과 J4_SIZE_ROWS(register_j4_motor_sizes.py 실제 등록값)
# 두 표가 서로 어긋나면, 같은 용량에 대해 J2S 유추 답변과 J4 실측 답변이 달라지는 조용한
# 데이터 불일치가 생긴다 — 두 표가 항상 같은 데이터임을 여기서 고정한다. ──
_table_as_rows = [
    (r["capacity_w"], r["hg_kr"], r["hg_mr"], r["frame_mm"], r["drive_a"], r["drive_b"])
    for r in J4_SIZE_TABLE
]
check("J4_SIZE_TABLE == register_j4_motor_sizes.J4_SIZE_ROWS",
      set(_table_as_rows), set(J4_SIZE_ROWS))

# ── _is_motor_model_name ──
check("HG- 는 모터", _is_motor_model_name("HG-KR43"), True)
check("HC- 는 모터", _is_motor_model_name("HC-KFS73"), True)
check("HA- 는 모터", _is_motor_model_name("HA-LFS502"), True)
check("MR- 는 드라이브(모터 아님)", _is_motor_model_name("MR-J4-70B"), False)
check("소문자 입력도 인식", _is_motor_model_name("hc-kfs43"), True)

# ── _split_brake_suffix ──
check("HC-KFS43B -> (HC-KFS43, True)", _split_brake_suffix("HC-KFS43B"), ("HC-KFS43", True))
check("HG-KR43B -> (HG-KR43, True)", _split_brake_suffix("HG-KR43B"), ("HG-KR43", True))
check("HC-SFS81 -> (HC-SFS81, False) 브레이크 아님", _split_brake_suffix("HC-SFS81"), ("HC-SFS81", False))
check("HC-LFS701M -> (HC-LFS701M, False) M은 브레이크 아님",
      _split_brake_suffix("HC-LFS701M"), ("HC-LFS701M", False))
check("MR-J4-70B는 호출측에서 애초에 걸러야 하지만, 함수 자체는 숫자+B 규칙만 적용",
      _split_brake_suffix("MR-J4-70B"), ("MR-J4-70", True))

if failures:
    print(f"\n{len(failures)}개 실패:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\n모든 테스트 통과.")
