# J2S→J4 서보모터 사이즈 유추 폴백 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a customer asks about a J2S-generation servo motor (HC-KFS/HC-SFS/HC-LFS 등) whose physical dimensions are not registered in `motor_specs`, answer with the flange-frame size and compatible MR-J4-xxA/xxB drives borrowed from the J4-series motor of the same wattage, with a brake-suffix (`B`)-aware note — without ever overriding a real, datasheet-sourced `motor_specs` entry when one exists.

**Architecture:** Add a static `J4_SIZE_TABLE` reference table and brake/motor-name helpers to `app/services/servo_spec_search.py`. Wire a new `_j2s_to_j4_size_note()` fallback into `find_reducer_compat()` as a third, lowest-priority branch (after the existing drive-match and motor-`motor_specs`-match branches), so every existing call site (`chatbot._route`) picks it up with zero routing changes. Also add a `register_j4_motor_sizes.py` migration script that registers the J4 table's own flange-frame values as real `motor_specs` entries on the already-existing `MR-J4-xxA`/`MR-J4-xxB` `Product` rows, so direct HG-KR/HG-MR queries get a real (non-"유추") answer too.

**Tech Stack:** Python 3, SQLAlchemy 2.0 async, existing repo conventions (no pytest — standalone `asyncio.run(main())` scripts).

## Global Constraints

- **Fallback only, never override real data.** If a queried motor already has a `motor_specs` entry (real datasheet dimensions, e.g. HC-KFS053/13/23/43 today, and HC-KFS73/HC-SFS*/HC-LFS* once the separate `feature/servo-response-restructure` worktree lands), that entry must always win. The new fallback only fires when no `motor_specs` entry matches at all.
- Do not touch the locked worktree `.claude/worktrees/feature+servo-response-restructure` — it has uncommitted in-progress work on a different branch.
- `B` suffix means "brake" only on **motor** model names (`HG-`, `HC-`, `HA-` prefixes). On **drive** model names (`MR-` prefix, e.g. `MR-J4-70B`) it means the SSCNET interface variant — never strip it there.
- Exact source data for the J4 table (verbatim from the user's spec):

  | 용량 | 형명(HG-KR) | 형명(HG-MR) | 플랜지프레임 | 서보드라이브A | 서보드라이브B |
  |---|---|---|---|---|---|
  | 50W | HG-KR053 | HG-MR053 | 40mm | MR-J4-10A | MR-J4-10B |
  | 100W | HG-KR13 | HG-MR13 | 40mm | MR-J4-10A | MR-J4-10B |
  | 200W | HG-KR23 | HG-MR23 | 60mm | MR-J4-20A | MR-J4-20B |
  | 400W | HG-KR43 | HG-MR43 | 60mm | MR-J4-40A | MR-J4-40B |
  | 750W | HG-KR73 | HG-MR73 | 80mm | MR-J4-70A | MR-J4-70B |
  | 500W | HG-SR52 | HG-MR52 | 130mm | MR-J4-60A | MR-J4-60B |
  | 1000W | HG-SR102 | HG-MR102 | 130mm | MR-J4-100A | MR-J4-100B |
  | 1500W | HG-SR152 | HG-MR152 | 130mm | MR-J4-200A | MR-J4-200B |
  | 2000W | HG-SR202 | HG-MR202 | 176mm | MR-J4-200A | MR-J4-200B |
  | 3500W | HG-SR352 | HG-MR352 | 176mm | MR-J4-350A | MR-J4-350B |
  | 5000W | HG-SR502 | HG-MR502 | 176mm | MR-J4-500A | MR-J4-500B |
  | 7000W | HG-SR702 | HG-MR702 | 176mm | MR-J4-700A | MR-J4-700B |

  Known pre-existing data mismatch to be aware of (not to silently "fix"): `register_mrj4_servo.py` already registered `MR-J4-60A`/`MR-J4-60B` with top-level `capacity_w=600` and `compatible_motors=["HG-SR51","HG-SR52"]`, while this table says HG-SR52 is 500W. Task 3 registers the table's values into `motor_specs` (which is per-motor, not the drive's top-level `capacity_w`) — this does not change the existing `capacity_w=600` field, so no data is overwritten, but the discrepancy will be visible if someone compares the two. Flag it to the user; do not silently alter either value.

---

## File Structure

- **Modify `app/services/servo_spec_search.py`**: add `J4_SIZE_TABLE` + `_J4_SIZE_BY_CAPACITY` (reference data), `_is_motor_model_name()`, `_split_brake_suffix()`, `_BRAKE_SAME_SIZE_NOTE` (helpers), `_find_capacity_w_by_compatible_motor()` and `_j2s_to_j4_size_note()` (fallback logic), and wire the brake note + fallback into `find_reducer_compat()`.
- **Create `test_j4_size_helpers.py`** (repo root): pure-function tests for the new helpers, no DB needed.
- **Create `test_servo_j2s_j4_fallback.py`** (repo root): DB-backed test (temp rows + rollback, following `test_servo_dimension_search.py`'s pattern) for the `find_reducer_compat()` integration — fallback text, brake note, and non-match cases.
- **Create `register_j4_motor_sizes.py`** (repo root): migration script registering the J4 table's `frame_size_mm`/`power_w` into `motor_specs` on the 12 existing `MR-J4-xxA`/`MR-J4-xxB` `Product` rows. Follows the `--dry-run` pattern from `register_motor_dimensions.py`.

---

### Task 1: J4 size table + brake/motor-name helpers (pure functions)

**Files:**
- Modify: `app/services/servo_spec_search.py:11` (right after `_KNOWN_CAPACITIES_W`)
- Modify: `app/services/servo_spec_search.py:30` (right after the `_REDUCER_ADAPTER_DISCLAIMER` block, before `_drive_family_key`)
- Test: `test_j4_size_helpers.py`

**Interfaces:**
- Produces: `J4_SIZE_TABLE: list[dict]`, `_J4_SIZE_BY_CAPACITY: dict[float, dict]`, `_is_motor_model_name(name: str) -> bool`, `_split_brake_suffix(motor_name: str) -> tuple[str, bool]`, `_BRAKE_SAME_SIZE_NOTE: str` — all consumed by Task 2.

- [ ] **Step 1: Write the failing test**

Create `C:\nextapp\hdauto-chatbot\test_j4_size_helpers.py`:

```python
"""
J4 사이즈 기준표 + 브레이크 접미사/모터형명 판별 헬퍼 단위 테스트 (순수 함수, DB 불필요).
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.services.servo_spec_search import (
    J4_SIZE_TABLE, _J4_SIZE_BY_CAPACITY, _is_motor_model_name, _split_brake_suffix,
)

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_j4_size_helpers.py`
Expected: `ImportError: cannot import name 'J4_SIZE_TABLE' from 'app.services.servo_spec_search'` (none of the new names exist yet).

- [ ] **Step 3: Add `J4_SIZE_TABLE` reference data**

In `app/services/servo_spec_search.py`, immediately after line 11 (`_KNOWN_CAPACITIES_W = [...]`), insert:

```python

# ─── J4 시리즈 기준 사이즈 테이블 (용량W → 형명(KR/MR)/플랜지프레임/서보드라이브A·B).
# J2S 등 타 시리즈 모터가 motor_specs에 실측 등록돼 있지 않을 때 "동일 용량이면 동일
# 프레임" 규칙으로 사이즈를 유추하는 폴백 근거로만 쓴다 — 실측 motor_specs 항목이 있으면
# 항상 그쪽이 우선(find_reducer_compat 참조). register_j4_motor_sizes.py가 이 표를 그대로
# MR-J4-xxA/xxB의 motor_specs에도 등록해, HG-KR/HG-MR 자체 조회도 실측 경로로 답한다. ───
J4_SIZE_TABLE = [
    {"capacity_w": 50, "hg_kr": "HG-KR053", "hg_mr": "HG-MR053", "frame_mm": 40, "drive_a": "MR-J4-10A", "drive_b": "MR-J4-10B"},
    {"capacity_w": 100, "hg_kr": "HG-KR13", "hg_mr": "HG-MR13", "frame_mm": 40, "drive_a": "MR-J4-10A", "drive_b": "MR-J4-10B"},
    {"capacity_w": 200, "hg_kr": "HG-KR23", "hg_mr": "HG-MR23", "frame_mm": 60, "drive_a": "MR-J4-20A", "drive_b": "MR-J4-20B"},
    {"capacity_w": 400, "hg_kr": "HG-KR43", "hg_mr": "HG-MR43", "frame_mm": 60, "drive_a": "MR-J4-40A", "drive_b": "MR-J4-40B"},
    {"capacity_w": 750, "hg_kr": "HG-KR73", "hg_mr": "HG-MR73", "frame_mm": 80, "drive_a": "MR-J4-70A", "drive_b": "MR-J4-70B"},
    {"capacity_w": 500, "hg_kr": "HG-SR52", "hg_mr": "HG-MR52", "frame_mm": 130, "drive_a": "MR-J4-60A", "drive_b": "MR-J4-60B"},
    {"capacity_w": 1000, "hg_kr": "HG-SR102", "hg_mr": "HG-MR102", "frame_mm": 130, "drive_a": "MR-J4-100A", "drive_b": "MR-J4-100B"},
    {"capacity_w": 1500, "hg_kr": "HG-SR152", "hg_mr": "HG-MR152", "frame_mm": 130, "drive_a": "MR-J4-200A", "drive_b": "MR-J4-200B"},
    {"capacity_w": 2000, "hg_kr": "HG-SR202", "hg_mr": "HG-MR202", "frame_mm": 176, "drive_a": "MR-J4-200A", "drive_b": "MR-J4-200B"},
    {"capacity_w": 3500, "hg_kr": "HG-SR352", "hg_mr": "HG-MR352", "frame_mm": 176, "drive_a": "MR-J4-350A", "drive_b": "MR-J4-350B"},
    {"capacity_w": 5000, "hg_kr": "HG-SR502", "hg_mr": "HG-MR502", "frame_mm": 176, "drive_a": "MR-J4-500A", "drive_b": "MR-J4-500B"},
    {"capacity_w": 7000, "hg_kr": "HG-SR702", "hg_mr": "HG-MR702", "frame_mm": 176, "drive_a": "MR-J4-700A", "drive_b": "MR-J4-700B"},
]
_J4_SIZE_BY_CAPACITY: dict[float, dict] = {row["capacity_w"]: row for row in J4_SIZE_TABLE}
```

- [ ] **Step 4: Add brake-suffix / motor-name helpers**

In the same file, immediately after the `_REDUCER_ADAPTER_DISCLAIMER` block (currently ends at line 30, right before `def _drive_family_key`), insert:

```python

_MOTOR_NAME_PREFIXES = ("HG-", "HC-", "HA-")
_BRAKE_SAME_SIZE_NOTE = "브레이크 내장 모델로 사이즈는 동일합니다."


def _is_motor_model_name(name: str) -> bool:
    """HG-/HC-/HA-로 시작하면 서보'모터' 형명, MR-로 시작하면 서보'드라이브' 형명.
    드라이브 쪽 B(예: MR-J4-70B)는 SSCNET 인터페이스를 뜻하므로 브레이크 판단 대상에서
    제외해야 한다 — 호출측은 이 함수로 먼저 걸러낸 뒤에만 _split_brake_suffix를 쓴다."""
    return name.strip().upper().startswith(_MOTOR_NAME_PREFIXES)


def _split_brake_suffix(motor_name: str) -> tuple[str, bool]:
    """모터 형명 끝의 브레이크 내장 접미사 'B'를 분리한다.
    숫자 바로 뒤에 오는 B만 브레이크로 간주한다(예: HC-KFS43B -> ("HC-KFS43", True)).
    'M' 등 다른 접미사나, 원래 숫자로 끝나는 형명은 그대로 반환한다."""
    stripped = motor_name.strip()
    if len(stripped) >= 2 and stripped[-1].upper() == "B" and stripped[-2].isdigit():
        return stripped[:-1], True
    return stripped, False
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python test_j4_size_helpers.py`
Expected: `모든 테스트 통과.` with no `[FAIL]` lines, exit code 0.

- [ ] **Step 6: Commit**

```bash
git add app/services/servo_spec_search.py test_j4_size_helpers.py
git commit -m "feat: J4 시리즈 사이즈 기준표 + 브레이크 접미사 헬퍼 추가"
```

---

### Task 2: Wire J2S→J4 fallback + brake note into `find_reducer_compat`

**Files:**
- Modify: `app/services/servo_spec_search.py:410-464` (`_motor_has_registered_specs` stays as-is; `find_reducer_compat` gets the new logic)
- Test: `test_servo_j2s_j4_fallback.py`

**Interfaces:**
- Consumes: `J4_SIZE_TABLE`, `_J4_SIZE_BY_CAPACITY`, `_is_motor_model_name`, `_split_brake_suffix`, `_BRAKE_SAME_SIZE_NOTE` (from Task 1); `_all_servo_rows(db)`, `_with_dimension_disclaimer(text)` (existing).
- Produces: `_find_capacity_w_by_compatible_motor(base_motor_name: str, rows: list) -> float | None`, `_j2s_to_j4_size_note(model_name: str, rows: list) -> str | None`. `find_reducer_compat()`'s existing signature/behavior is unchanged for already-passing cases; it gains a third fallback branch and a brake note on branch 2 matches.

- [ ] **Step 1: Write the failing test**

Create `C:\nextapp\hdauto-chatbot\test_servo_j2s_j4_fallback.py`:

```python
"""
find_reducer_compat()의 J2S->J4 사이즈 유추 폴백 + 브레이크 접미사 안내 검증.

실제 DB 세션에 임시 Product/Specification을 추가하지만 db.commit()을 호출하지 않고
마지막에 db.rollback()으로 되돌리므로, 실행해도 DB에 영구 흔적이 남지 않는다.
(test_servo_dimension_search.py와 동일한 패턴)
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.db.database import async_session
from app.db.models import Product, Specification, ProductStatus
from app.services.servo_spec_search import find_reducer_compat, _DIMENSION_DISCLAIMER

# 400W로 세팅 — J4_SIZE_TABLE 기준 60mm 프레임 / MR-J4-40A,B와 매칭되어야 함
TEST_DRIVE_MODEL = "MR-TESTJ2S-99A"
TEST_MOTOR_BASE = "HC-TESTMOTOR99"
TEST_MOTOR_BRAKE = "HC-TESTMOTOR99B"

# J4 표에 없는 용량(850W) — 폴백이 발동하면 안 됨
TEST_DRIVE_NOMATCH = "MR-TESTJ2S-88A"
TEST_MOTOR_NOMATCH = "HC-TESTMOTOR88"


async def _seed(db) -> None:
    product = Product(
        model_name=TEST_DRIVE_MODEL, series="TEST-J2S", manufacturer="TestMfr",
        category="servo", status=ProductStatus.ACTIVE,
    )
    db.add(product)
    await db.flush()
    db.add(Specification(
        product_id=product.id,
        extra_specs={"capacity_w": 400, "compatible_motors": [TEST_MOTOR_BASE]},
    ))

    product2 = Product(
        model_name=TEST_DRIVE_NOMATCH, series="TEST-J2S", manufacturer="TestMfr",
        category="servo", status=ProductStatus.ACTIVE,
    )
    db.add(product2)
    await db.flush()
    db.add(Specification(
        product_id=product2.id,
        extra_specs={"capacity_w": 850, "compatible_motors": [TEST_MOTOR_NOMATCH]},
    ))
    await db.flush()


async def main():
    async with async_session() as db:
        await _seed(db)
        failures = []
        try:
            print("=" * 60)
            print(f"1) 브레이크 없는 J2S 모터 폴백: {TEST_MOTOR_BASE}")
            result = await find_reducer_compat(TEST_MOTOR_BASE, db)
            print(result)
            ok = (
                result is not None
                and "J2S 시리즈이지만" in result
                and "HG-KR43" in result
                and "60mm" in result
                and "MR-J4-40A" in result and "MR-J4-40B" in result
                and "브레이크 내장 모델로 사이즈는 동일합니다" not in result
                and _DIMENSION_DISCLAIMER in result
            )
            print("PASS" if ok else "FAIL")
            if not ok:
                failures.append("case1")

            print("=" * 60)
            print(f"2) 브레이크 있는 J2S 모터 폴백: {TEST_MOTOR_BRAKE}")
            result = await find_reducer_compat(TEST_MOTOR_BRAKE, db)
            print(result)
            ok = (
                result is not None
                and "J2S 시리즈이지만" in result
                and "MR-J4-40A" in result
                and "브레이크 내장 모델로 사이즈는 동일합니다" in result
            )
            print("PASS" if ok else "FAIL")
            if not ok:
                failures.append("case2")

            print("=" * 60)
            print(f"3) J4 표에 없는 용량(850W)은 폴백 없이 None: {TEST_MOTOR_NOMATCH}")
            result = await find_reducer_compat(TEST_MOTOR_NOMATCH, db)
            print(result)
            ok = result is None
            print("PASS" if ok else "FAIL")
            if not ok:
                failures.append("case3")

            print("=" * 60)
            print("4) J4 계열 자체 모델(HG-KR43)은 이 폴백 대상이 아님(motor_specs 실측 우선)")
            result = await find_reducer_compat("HG-KR43", db)
            print(result)
            ok = result is None or "J2S 시리즈이지만" not in result
            print("PASS" if ok else "FAIL")
            if not ok:
                failures.append("case4")
        finally:
            await db.rollback()

        if failures:
            print(f"\n{len(failures)}개 실패: {failures}")
            sys.exit(1)
        print("\n모든 테스트 통과.")


asyncio.run(main())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_servo_j2s_j4_fallback.py`
Expected: case 1 prints `None` (no fallback exists yet) → `FAIL`, `sys.exit(1)`.

- [ ] **Step 3: Add `_find_capacity_w_by_compatible_motor` and `_j2s_to_j4_size_note`**

In `app/services/servo_spec_search.py`, immediately before `async def find_reducer_compat` (currently line 422), insert:

```python
def _find_capacity_w_by_compatible_motor(base_motor_name: str, rows: list) -> float | None:
    """base_motor_name이 어느 드라이브의 compatible_motors에 정확히(대소문자 무시) 등록돼
    있는지 찾아 그 드라이브의 capacity_w를 반환한다. 부분일치가 아닌 완전일치만 쓴다 —
    이 값이 J4 사이즈 유추의 근거가 되므로, 느슨한 매칭으로 엉뚱한 용량을 끌어오면 안 된다."""
    key = base_motor_name.strip().lower()
    for _, s in rows:
        if not s.extra_specs:
            continue
        motors = s.extra_specs.get("compatible_motors", [])
        if any(key == m.strip().lower() for m in motors):
            return s.extra_specs.get("capacity_w")
    return None


def _j2s_to_j4_size_note(model_name: str, rows: list) -> str | None:
    """J2S 등 타 시리즈 서보모터가 motor_specs에 실측 등록돼 있지 않을 때, 같은 용량의
    J4 시리즈 모터와 플랜지 프레임 사이즈가 동일하다는 규칙으로 사이즈+대응 서보드라이브를
    유추해 안내 문구를 만든다. find_reducer_compat()가 실측 motor_specs 매칭에 모두
    실패한 뒤에만 호출하는 순수 폴백이다. 매칭 근거(같은 용량의 J4 표 행)가 없으면
    None을 반환해 "찾지 못함" 상태를 그대로 유지한다(추측으로 채우지 않음)."""
    if not _is_motor_model_name(model_name):
        return None

    base, has_brake = _split_brake_suffix(model_name)

    if base.upper().startswith("HG-"):
        return None  # J4 계열 자체 모델은 실측 등록 대상 — 이 폴백을 타지 않는다.

    capacity_w = _find_capacity_w_by_compatible_motor(base, rows)
    if capacity_w is None:
        return None

    j4_row = _J4_SIZE_BY_CAPACITY.get(capacity_w)
    if j4_row is None:
        return None

    lines = [
        f"J2S 시리즈이지만 J4 시리즈의 {j4_row['hg_kr']}({capacity_w:g}W) 모델과 사이즈가 "
        f"동일하여 해당 값을 기준으로 안내드립니다.",
        f"플랜지 프레임: □{j4_row['frame_mm']}mm",
        f"대응 서보드라이브: {j4_row['drive_a']}, {j4_row['drive_b']}",
    ]
    if has_brake:
        lines.append(_BRAKE_SAME_SIZE_NOTE)

    return "\n".join(lines)
```

- [ ] **Step 4: Add brake note to the existing motor_specs match branch, and wire the fallback**

In `find_reducer_compat` (currently lines 422-464), the body currently reads (step markers added for reference):

```python
async def find_reducer_compat(model_name: str, db: AsyncSession) -> str | None:
    rows = await _all_servo_rows(db)
    reducer_rows = await _all_reducer_rows(db)
    key = model_name.strip().lower()

    # 1) 드라이브 자체 모델명/시리즈로 매칭
    for p, s in rows:
        if key in p.model_name.lower() or (p.series and key in p.series.lower()):
            motor_specs = (s.extra_specs or {}).get("motor_specs") or {}
            if motor_specs:
                blocks = [_format_motor_spec_block(m, d, reducer_rows) for m, d in motor_specs.items()]
                header = f"**{p.manufacturer} {p.model_name}** 호환 모터 결합사양:"
                return _with_dimension_disclaimer(f"{header}\n\n" + "\n\n".join(blocks))

    # 2) 모터명으로 매칭 (여러 드라이브에 걸쳐 등록돼 있을 수 있음 — 모두 수집)
    matched_blocks = []
    seen_families: set[str] = set()
    for p, s in rows:
        motor_specs = (s.extra_specs or {}).get("motor_specs") or {}
        for motor_key, motor_data in motor_specs.items():
            if key in motor_key.lower() or motor_key.lower() in key:
                family = _drive_family_key(p.model_name)
                if family in seen_families:
                    continue
                seen_families.add(family)
                block = _format_motor_spec_block(motor_key, motor_data, reducer_rows)
                matched_blocks.append(f"(호환 드라이브: {p.manufacturer} {p.model_name})\n{block}")

    if not matched_blocks:
        return None

    header = f"**{model_name}** 결합사양:"
    return _with_dimension_disclaimer(f"{header}\n\n" + "\n\n".join(matched_blocks))
```

Replace it with:

```python
async def find_reducer_compat(model_name: str, db: AsyncSession) -> str | None:
    rows = await _all_servo_rows(db)
    reducer_rows = await _all_reducer_rows(db)
    key = model_name.strip().lower()

    # 1) 드라이브 자체 모델명/시리즈로 매칭
    for p, s in rows:
        if key in p.model_name.lower() or (p.series and key in p.series.lower()):
            motor_specs = (s.extra_specs or {}).get("motor_specs") or {}
            if motor_specs:
                blocks = [_format_motor_spec_block(m, d, reducer_rows) for m, d in motor_specs.items()]
                header = f"**{p.manufacturer} {p.model_name}** 호환 모터 결합사양:"
                return _with_dimension_disclaimer(f"{header}\n\n" + "\n\n".join(blocks))

    # 2) 모터명으로 매칭 (여러 드라이브에 걸쳐 등록돼 있을 수 있음 — 모두 수집)
    _, query_has_brake = (
        _split_brake_suffix(model_name) if _is_motor_model_name(model_name) else (model_name, False)
    )
    matched_blocks = []
    seen_families: set[str] = set()
    for p, s in rows:
        motor_specs = (s.extra_specs or {}).get("motor_specs") or {}
        for motor_key, motor_data in motor_specs.items():
            if key in motor_key.lower() or motor_key.lower() in key:
                family = _drive_family_key(p.model_name)
                if family in seen_families:
                    continue
                seen_families.add(family)
                block = _format_motor_spec_block(motor_key, motor_data, reducer_rows)
                if query_has_brake:
                    block += f"\n※ {_BRAKE_SAME_SIZE_NOTE}"
                matched_blocks.append(f"(호환 드라이브: {p.manufacturer} {p.model_name})\n{block}")

    if matched_blocks:
        header = f"**{model_name}** 결합사양:"
        return _with_dimension_disclaimer(f"{header}\n\n" + "\n\n".join(matched_blocks))

    # 3) 실측 motor_specs가 전혀 없는 J2S 등 타 시리즈 모터 -> J4 동일 용량 사이즈 유추 폴백
    j2s_note = _j2s_to_j4_size_note(model_name, rows)
    if j2s_note:
        header = f"**{model_name}** 결합사양(J4 시리즈 기준 유추):"
        return _with_dimension_disclaimer(f"{header}\n\n{j2s_note}")

    return None
```

(Leave step 1, the drive-model-match branch, untouched — only step 2's loop body and the final return path change.)

- [ ] **Step 5: Run test to verify it passes**

Run: `python test_servo_j2s_j4_fallback.py`
Expected: `모든 테스트 통과.`, exit code 0.

- [ ] **Step 6: Regression-check the pre-existing dimension test still passes**

Run: `python test_servo_dimension_search.py`
Expected: `모든 테스트 통과.` (this exercises the same `find_reducer_compat` function's steps 1/2 with real `motor_specs`/`reducers` data — must still pass unchanged, proving step 2 and the disclaimer logic weren't broken).

- [ ] **Step 7: Commit**

```bash
git add app/services/servo_spec_search.py test_servo_j2s_j4_fallback.py
git commit -m "feat: J2S 서보모터 문의에 J4 동일용량 사이즈 유추 폴백 추가"
```

---

### Task 3: Register J4 motor flange-frame sizes as real `motor_specs`

**Files:**
- Create: `register_j4_motor_sizes.py`

**Interfaces:**
- Consumes: `app.db.database.async_session`, `app.db.models.Product`, `app.db.models.Specification` (existing).
- Produces: nothing consumed by later tasks — this is the terminal data-registration step.

- [ ] **Step 1: Write `register_j4_motor_sizes.py`**

Create `C:\nextapp\hdauto-chatbot\register_j4_motor_sizes.py`:

```python
"""
J4 시리즈(HG-KR/HG-MR) 서보모터 플랜지 프레임 사이즈 등록.

사용자 제공 기준표(용량-형명(KR)-형명(MR)-플랜지프레임-서보드라이브A/B)를 그대로
motor_specs에 반영한다. 실측 상세 전기사양(토크/관성/전장 등) 출처가 없어
frame_size_mm(플랜지 프레임)과 power_w만 등록한다 — register_mrj4_servo.py가 이미
등록해둔 MR-J4-xxA/xxB Product 행에 motor_specs로 병합된다(기존 capacity_w/
compatible_motors 등은 보존).

이 표는 app/services/servo_spec_search.py의 J4_SIZE_TABLE(J2S->J4 사이즈 유추 폴백의
근거 표)과 동일한 원본 데이터다 — HG-KR/HG-MR 형명 자체를 직접 조회했을 때도 "유추"가
아닌 실측 경로(find_reducer_compat 2단계)로 같은 값이 나오게 하기 위함.

알려진 불일치(임의로 고치지 않고 그대로 둠): register_mrj4_servo.py는 MR-J4-60A/60B를
capacity_w=600, compatible_motors=["HG-SR51","HG-SR52"]로 이미 등록했으나, 이 표는
HG-SR52를 500W로 명시한다. 이 스크립트는 드라이브의 top-level capacity_w는 건드리지
않고 motor_specs 하위 항목에만 power_w=500을 기록하므로 기존 값을 덮어쓰지 않는다.

실행: python register_j4_motor_sizes.py [--dry-run]
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Product, Specification

# (용량W, 형명KR, 형명MR, 플랜지프레임mm, 드라이브A, 드라이브B)
J4_SIZE_ROWS = [
    (50, "HG-KR053", "HG-MR053", 40, "MR-J4-10A", "MR-J4-10B"),
    (100, "HG-KR13", "HG-MR13", 40, "MR-J4-10A", "MR-J4-10B"),
    (200, "HG-KR23", "HG-MR23", 60, "MR-J4-20A", "MR-J4-20B"),
    (400, "HG-KR43", "HG-MR43", 60, "MR-J4-40A", "MR-J4-40B"),
    (750, "HG-KR73", "HG-MR73", 80, "MR-J4-70A", "MR-J4-70B"),
    (500, "HG-SR52", "HG-MR52", 130, "MR-J4-60A", "MR-J4-60B"),
    (1000, "HG-SR102", "HG-MR102", 130, "MR-J4-100A", "MR-J4-100B"),
    (1500, "HG-SR152", "HG-MR152", 130, "MR-J4-200A", "MR-J4-200B"),
    (2000, "HG-SR202", "HG-MR202", 176, "MR-J4-200A", "MR-J4-200B"),
    (3500, "HG-SR352", "HG-MR352", 176, "MR-J4-350A", "MR-J4-350B"),
    (5000, "HG-SR502", "HG-MR502", 176, "MR-J4-500A", "MR-J4-500B"),
    (7000, "HG-SR702", "HG-MR702", 176, "MR-J4-700A", "MR-J4-700B"),
]


def _entry(capacity_w: int, frame_mm: int) -> dict:
    return {
        "power_w": capacity_w,
        "dimensions": {"frame_size_mm": frame_mm},
        "reducers": [],
    }


async def _merge_motor_specs(db, model_name: str, motor_entries: dict) -> str:
    result = await db.execute(select(Product).where(Product.model_name == model_name))
    product = result.scalar_one_or_none()
    if not product:
        return f"스킵 (드라이브 행 없음): {model_name}"

    spec_result = await db.execute(select(Specification).where(Specification.product_id == product.id))
    spec = spec_result.scalar_one_or_none()
    if not spec:
        return f"스킵 (Specification 없음): {model_name}"

    extra_specs = dict(spec.extra_specs or {})
    motor_specs = dict(extra_specs.get("motor_specs") or {})
    motor_specs.update(motor_entries)
    extra_specs["motor_specs"] = motor_specs
    spec.extra_specs = extra_specs

    return f"병합 완료: {model_name} <- {list(motor_entries.keys())}"


async def main(dry_run: bool = False):
    async with async_session() as db:
        for capacity_w, hg_kr, hg_mr, frame_mm, drive_a, drive_b in J4_SIZE_ROWS:
            motor_entries = {
                hg_kr: _entry(capacity_w, frame_mm),
                hg_mr: _entry(capacity_w, frame_mm),
            }
            for model_name in (drive_a, drive_b):
                msg = await _merge_motor_specs(db, model_name, motor_entries)
                print(msg)

        if dry_run:
            await db.rollback()
            print("\n[DRY RUN] 커밋하지 않음 — 위 결과는 실제로 반영되지 않았습니다.")
        else:
            await db.commit()
            print("\n완료 — J4 시리즈(HG-KR/HG-MR) 12종 플랜지 프레임 사이즈 등록")


if __name__ == "__main__":
    asyncio.run(main(dry_run="--dry-run" in sys.argv))
```

- [ ] **Step 2: Run in dry-run mode and inspect output**

Run: `python register_j4_motor_sizes.py --dry-run`
Expected: 24 `병합 완료: MR-J4-xxA/B <- ['HG-KR..', 'HG-MR..']` lines (or `스킵 (드라이브 행 없음)` for any MR-J4 model not present in the connected DB), followed by `[DRY RUN] 커밋하지 않음 — ...`.

- [ ] **Step 3: Show the dry-run output to the user and ask before running for real**

This step writes to the shared production database (`DATABASE_URL` in `.env`, reached over Tailscale per `CLAUDE.md`). Do not run without `--dry-run` until the user has seen the dry-run output above and explicitly confirms.

- [ ] **Step 4: Run for real (only after user confirmation)**

Run: `python register_j4_motor_sizes.py`
Expected: same `병합 완료`/`스킵` lines, followed by `완료 — J4 시리즈(HG-KR/HG-MR) 12종 플랜지 프레임 사이즈 등록`.

- [ ] **Step 5: Commit**

```bash
git add register_j4_motor_sizes.py
git commit -m "feat: J4 시리즈 서보모터 플랜지 프레임 사이즈 등록 스크립트 추가"
```

---

## Self-Review Notes

- **Spec coverage:** §1 (J4 기준 데이터) → Task 1 (`J4_SIZE_TABLE`) + Task 3 (real registration). §2.1 (J4가 기준) → `_j2s_to_j4_size_note` always reads from `J4_SIZE_TABLE`. §2.2 (J2S→J4 대응 + 근거 문구) → `_j2s_to_j4_size_note`'s "J2S 시리즈이지만 ... 사이즈가 동일하여 ..." line. §2.3 (서보드라이브 A/B 안내) → `drive_a`/`drive_b` in the same note, and already-existing behavior for direct J4 motor matches via Task 3's registration. §2.4 (브레이크 처리) → `_split_brake_suffix` + `_BRAKE_SAME_SIZE_NOTE`, wired into both the fallback (Task 2) and the existing real-data match branch (Task 2 Step 4). §3 (기존 코드 구조 확인, dry-run 지원) → done above; `register_j4_motor_sizes.py` mirrors `register_motor_dimensions.py`'s `--dry-run` pattern exactly.
- **Placeholder scan:** none found — all steps contain full code/commands.
- **Type consistency:** `_j2s_to_j4_size_note(model_name: str, rows: list) -> str | None` and `_find_capacity_w_by_compatible_motor(base_motor_name: str, rows: list) -> float | None` are defined in Task 2 Step 3 and used identically in Task 2 Step 4's `find_reducer_compat` rewrite. `J4_SIZE_TABLE` / `_J4_SIZE_BY_CAPACITY` defined in Task 1 Step 3, consumed in Task 2 Step 3 and Task 3 (as the literal `J4_SIZE_ROWS`, kept as a plain tuple list there per the existing `register_*.py` script convention rather than importing the service-module dict).
