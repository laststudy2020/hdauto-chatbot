# APEX AB/ABR 감속기 카탈로그 + 축경 자동매칭 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** APEX AB/ABR 감속기 카탈로그를 새 `Reducer` DB 테이블에 등록하고, 서보모터 축경(shaft_diameter_mm) 기준 자동매칭을 `servo_spec_search.py`의 감속기 결합사양 조회 흐름에 추가한다.

**Architecture:** 새 독립 `Reducer` 테이블(Product/Inventory와 무관, 순수 참고 데이터) → 등록 스크립트로 30행 시딩 → `_format_motor_spec_block`이 curated `reducers` 리스트가 비어있을 때 축경 기준 자동매칭으로 폴백 → 매칭/미매칭/미시도 세 상태를 각각 다른 문구로 구분.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0 async, MariaDB(asyncmy)/SQLite(aiosqlite), pdfplumber(데이터 소스 조사용, 런타임에는 불필요).

## Global Constraints

- 이 프로젝트에는 pytest가 없다. 검증은 기존 관례대로 `asyncio.run(main())` 스크립트를 직접 실행하고 assert로 확인한다 (CLAUDE.md 참고).
- Windows 콘솔은 cp949라 특수문자(—, ✓, ⚠️, 🔩 등)를 `print()`하는 새 스크립트는 반드시 상단에 `sys.stdout.reconfigure(encoding="utf-8")`를 넣는다 (이전 세션에서 `register_hc_kfs_servo.py`가 이 문제로 크래시했던 것과 동일 원인 — 커밋 없이도 재발하므로 매 스크립트에 필수).
- `.env`의 `DATABASE_URL`은 프로덕션 NAS MariaDB를 가리킨다 (Tailscale IP `100.109.19.49`). 이 플랜의 모든 DB 쓰기(Task 2의 register 스크립트 실행)는 프로덕션에 직접 반영된다 — 실행 전 `Reducer` 테이블은 신규 테이블이라 기존 데이터 백업이 필요 없다(새로 생기는 테이블이므로 롤백은 "테이블 DROP"이면 충분).
- `Reducer`는 `Product`/`Inventory`와 관계를 맺지 않는다 (순수 참고 데이터, 사용자 확정).
- C1~C10(모터 장착 어댑터 치수)은 어떤 컬럼에도 저장하지 않는다 — 매칭 로직이 이 값을 쓰지 않기 때문.
- 매칭 알고리즘은 "축경 이하인 것 전부"가 아니라 "축경을 수용하는 가장 작은 입력홀 등급만" 반환한다 (설계 문서 참고 — 그렇지 않으면 14mm 모터에도 AB220 같은 대형 감속기가 걸림).
- 데이터 출처: `docs/superpowers/specs/2026-07-11-apex-reducer-matching-design.md`의 "데이터 확정값" 표 — PDF 페이지를 300dpi 이미지로 렌더링해 육안 확인한 최종값. 이 플랜의 Task 2 코드에 그대로 반영돼 있으므로 재조사 불필요.

---

### Task 1: `Reducer` DB 모델 추가

**Files:**
- Modify: `app/db/models.py:154-156`

**Interfaces:**
- Produces: `app.db.models.Reducer` 클래스 (컬럼: `id, series, model_name, stage, ratio_list, ratio_range_label, input_bore_std_mm, input_bore_optional_mm, rated_torque_min_nm, rated_torque_max_nm, rated_input_speed_rpm, max_input_speed_rpm, weight_kg, backlash_p0_arcmin, backlash_p1_arcmin, backlash_p2_arcmin, backlash_note, source_note`). Task 2가 이 클래스로 행을 INSERT하고, Task 3이 이 클래스로 SELECT한다.

- [ ] **Step 1: `app/db/models.py`에 `Reducer` 클래스 추가**

`app/db/models.py`의 154번째 줄(`AlarmCode` 클래스의 마지막 필드 `manual_filename = Column(String(200))  # 원본 PDF 파일명`) 바로 다음, `class KakaoToken(Base):` 앞에 삽입:

```python

# ─── 8. APEX AB/ABR 감속기 카탈로그 (참고 데이터 — Product/Inventory와 무관) ───
class Reducer(Base):
    __tablename__ = "reducers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    series = Column(String(10), nullable=False, index=True)        # "AB" | "ABR"
    model_name = Column(String(20), nullable=False, index=True)    # "AB042", "AB060A", ...
    stage = Column(Integer, nullable=False)                        # 1 | 2
    ratio_list = Column(JSON)               # 선택 가능 감속비 리스트, 예: [3,4,5,6,7,8,9,10]
    ratio_range_label = Column(String(20))  # 표시용, 예: "3~10"

    input_bore_std_mm = Column(Float)       # 표준 입력홀 최대 허용 축경(mm)
    input_bore_optional_mm = Column(Float)  # 옵션 주문시 최대 허용 축경(mm), 없으면 NULL

    rated_torque_min_nm = Column(Float)     # 감속비 구간 내 정격출력토크 범위
    rated_torque_max_nm = Column(Float)

    rated_input_speed_rpm = Column(Integer)
    max_input_speed_rpm = Column(Integer)

    weight_kg = Column(Float)

    backlash_p0_arcmin = Column(Float)   # nullable — 미생산/특수주문이면 None
    backlash_p1_arcmin = Column(Float)
    backlash_p2_arcmin = Column(Float)
    backlash_note = Column(String(200))  # "P0급 제작안됨" 등 각주 보존

    source_note = Column(String(200))    # 출처, 예: "apex감속기 06AB+Series.pdf p.71(spec)/p.72(dim)"
```

- [ ] **Step 2: 테이블 생성 확인**

`app/db/database.py`의 `init_db()`는 `Base.metadata.create_all`을 쓰므로 기존 테이블에 영향 없이 새 테이블만 추가 생성된다. 직접 호출해서 확인:

Run:
```bash
cd "C:/nextapp/hdauto-chatbot" && PYTHONIOENCODING=utf-8 "C:/Users/last_/AppData/Local/Programs/Python/Python313/python.exe" -c "
import asyncio
from app.db.database import init_db, engine

async def main():
    await init_db()
    async with engine.connect() as conn:
        result = await conn.exec_driver_sql(\"SHOW TABLES LIKE 'reducers'\")
        print('reducers table exists:', result.fetchone() is not None)

asyncio.run(main())
"
```
Expected: `reducers table exists: True` (SQLite 로컬 환경이면 `SHOW TABLES` 대신 `SELECT name FROM sqlite_master WHERE type='table' AND name='reducers'`로 바꿔서 확인 — `.env`의 `DATABASE_URL`이 `mysql+asyncmy://`인지 `sqlite+aiosqlite://`인지 먼저 확인하고 맞는 쪽 사용).

- [ ] **Step 3: 커밋**

```bash
cd "C:/nextapp/hdauto-chatbot" && git add app/db/models.py && git commit -m "$(cat <<'EOF'
feat: APEX AB/ABR 감속기 카탈로그용 Reducer 테이블 추가

Product/Inventory와 무관한 순수 참고 데이터 테이블. 축경 기반
자동매칭(다음 커밋)이 이 테이블을 조회한다.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `register_apex_reducer.py` 작성 + 실행

**Files:**
- Create: `register_apex_reducer.py`

**Interfaces:**
- Consumes: `app.db.models.Reducer` (Task 1)
- Produces: DB `reducers` 테이블에 30행 (AB 16행 + ABR 14행). Task 3~5는 이 데이터를 전제로 매칭 로직/테스트를 작성한다.

- [ ] **Step 1: `register_apex_reducer.py` 작성**

```python
"""
APEX AB/ABR 시리즈 planetary gearbox(감속기) 카탈로그 등록.

데이터 출처: docs/datasheets/apex_reduer/apex감속기 06AB+Series.pdf
- p.71: AB 시리즈 Gearbox Performance (정격토크/입력회전수/백래시/무게 등)
- p.72: AB 1단 감속(i=3~10) Dimension (입력홀 규격 C3 포함)
- p.73: AB 2단 감속(i=15~100) Dimension
- p.74: ABR 시리즈 Gearbox Performance
- p.75: ABR 1단 감속(i=3~20) Dimension
- p.76: ABR 2단 감속(i=25~200) Dimension

표 추출이 줄바꿈으로 깨졌던 셀(AB060A/AB090A 2단 입력홀, ABR 2단 입력홀)은
페이지를 300dpi 이미지로 렌더링해 육안으로 재확인한 값 — 확정값이며 추측 아님
(docs/superpowers/specs/2026-07-11-apex-reducer-matching-design.md의
"데이터 확정값" 표와 동일).

이번 등록에서 제외한 것:
- C1~C10 (모터 장착 어댑터 치수): 카탈로그 각주 "C1~C10은 적용모터에 따라
  다릅니다. 당사 홈페이지... Design Tool을 이용하여 확인"에 따라 이 카탈로그
  값만으로는 확정 불가 — 컬럼 자체를 두지 않는다. 매칭 로직(servo_spec_search.py의
  find_reducer_compat)도 이 값을 쓰지 않고, 매칭 결과에는 항상
  "정확한 모터 장착 어댑터(C1~C10)는 APEX 측 확인이 필요합니다" 안내를 자동 첨부한다.
- P0=★ (AB090 2단): 표준 미생산이지만 "고객 요청 시 최대한 정밀하게 제작 가능
  (납기/가격 변동)" — backlash_p0_arcmin=None, backlash_note로 별도 보존.

실행: python register_apex_reducer.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.db.database import async_session
from app.db.models import Reducer

_AB1_RATIOS = [3, 4, 5, 6, 7, 8, 9, 10]
_AB2_RATIOS = [15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100]
_ABR1_RATIOS = [3, 4, 5, 6, 7, 8, 9, 10, 14, 20]
_ABR2_RATIOS = [25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100, 120, 140, 160, 180, 200]

_AB_SOURCE = "apex감속기 06AB+Series.pdf p.71(spec)/{dim_page}(dim)"
_ABR_SOURCE = "apex감속기 06AB+Series.pdf p.74(spec)/{dim_page}(dim)"

REDUCER_ROWS = [
    # ── AB 1단 (i=3~10), p.72 ──
    dict(series="AB", model_name="AB042", stage=1, ratio_list=_AB1_RATIOS, ratio_range_label="3~10",
         input_bore_std_mm=11, input_bore_optional_mm=12,
         rated_torque_min_nm=14, rated_torque_max_nm=22,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=0.6,
         backlash_p0_arcmin=None, backlash_p1_arcmin=3, backlash_p2_arcmin=5,
         backlash_note="P0급 제작안됨", source_note=_AB_SOURCE.format(dim_page="p.72")),
    dict(series="AB", model_name="AB060", stage=1, ratio_list=_AB1_RATIOS, ratio_range_label="3~10",
         input_bore_std_mm=14, input_bore_optional_mm=16,
         rated_torque_min_nm=40, rated_torque_max_nm=60,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=1.3,
         backlash_p0_arcmin=None, backlash_p1_arcmin=3, backlash_p2_arcmin=5,
         backlash_note="P0급 제작안됨", source_note=_AB_SOURCE.format(dim_page="p.72")),
    dict(series="AB", model_name="AB090", stage=1, ratio_list=_AB1_RATIOS, ratio_range_label="3~10",
         input_bore_std_mm=19, input_bore_optional_mm=24,
         rated_torque_min_nm=100, rated_torque_max_nm=160,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=3.7,
         backlash_p0_arcmin=1, backlash_p1_arcmin=3, backlash_p2_arcmin=5,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.72")),
    dict(series="AB", model_name="AB115", stage=1, ratio_list=_AB1_RATIOS, ratio_range_label="3~10",
         input_bore_std_mm=32, input_bore_optional_mm=None,
         rated_torque_min_nm=230, rated_torque_max_nm=330,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=7.8,
         backlash_p0_arcmin=1, backlash_p1_arcmin=3, backlash_p2_arcmin=5,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.72")),
    dict(series="AB", model_name="AB142", stage=1, ratio_list=_AB1_RATIOS, ratio_range_label="3~10",
         input_bore_std_mm=38, input_bore_optional_mm=None,
         rated_torque_min_nm=342, rated_torque_max_nm=650,
         rated_input_speed_rpm=3000, max_input_speed_rpm=6000, weight_kg=14.5,
         backlash_p0_arcmin=1, backlash_p1_arcmin=3, backlash_p2_arcmin=5,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.72")),
    dict(series="AB", model_name="AB180", stage=1, ratio_list=_AB1_RATIOS, ratio_range_label="3~10",
         input_bore_std_mm=48, input_bore_optional_mm=None,
         rated_torque_min_nm=588, rated_torque_max_nm=1200,
         rated_input_speed_rpm=3000, max_input_speed_rpm=6000, weight_kg=29,
         backlash_p0_arcmin=1, backlash_p1_arcmin=3, backlash_p2_arcmin=5,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.72")),
    dict(series="AB", model_name="AB220", stage=1, ratio_list=_AB1_RATIOS, ratio_range_label="3~10",
         input_bore_std_mm=55, input_bore_optional_mm=None,
         rated_torque_min_nm=1140, rated_torque_max_nm=2000,
         rated_input_speed_rpm=2000, max_input_speed_rpm=4000, weight_kg=48,
         backlash_p0_arcmin=1, backlash_p1_arcmin=3, backlash_p2_arcmin=5,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.72")),

    # ── AB 2단 (i=15~100), p.73 ──
    dict(series="AB", model_name="AB042", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=11, input_bore_optional_mm=12,
         rated_torque_min_nm=14, rated_torque_max_nm=22,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=0.8,
         backlash_p0_arcmin=None, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note="P0급 제작안됨", source_note=_AB_SOURCE.format(dim_page="p.73")),
    dict(series="AB", model_name="AB060", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=11, input_bore_optional_mm=12,
         rated_torque_min_nm=40, rated_torque_max_nm=60,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=1.5,
         backlash_p0_arcmin=None, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note="P0급 제작안됨", source_note=_AB_SOURCE.format(dim_page="p.73")),
    dict(series="AB", model_name="AB060A", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=14, input_bore_optional_mm=16,
         rated_torque_min_nm=40, rated_torque_max_nm=60,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=1.9,
         backlash_p0_arcmin=None, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note="P0급 제작안됨 (Special type)", source_note=_AB_SOURCE.format(dim_page="p.73")),
    dict(series="AB", model_name="AB090", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=14, input_bore_optional_mm=16,
         rated_torque_min_nm=100, rated_torque_max_nm=160,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=4.1,
         backlash_p0_arcmin=None, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note="P0급: 고객 요청 시 특별 제작 가능(★, 납기/가격 변동)",
         source_note=_AB_SOURCE.format(dim_page="p.73")),
    dict(series="AB", model_name="AB090A", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=19, input_bore_optional_mm=24,
         rated_torque_min_nm=100, rated_torque_max_nm=160,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=5.3,
         backlash_p0_arcmin=None, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note="P0급 제작안됨 (Special type)", source_note=_AB_SOURCE.format(dim_page="p.73")),
    dict(series="AB", model_name="AB115", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=19, input_bore_optional_mm=24,
         rated_torque_min_nm=230, rated_torque_max_nm=330,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=9,
         backlash_p0_arcmin=3, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.73")),
    dict(series="AB", model_name="AB142", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=32, input_bore_optional_mm=None,
         rated_torque_min_nm=342, rated_torque_max_nm=650,
         rated_input_speed_rpm=3000, max_input_speed_rpm=6000, weight_kg=17.5,
         backlash_p0_arcmin=3, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.73")),
    dict(series="AB", model_name="AB180", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=38, input_bore_optional_mm=None,
         rated_torque_min_nm=588, rated_torque_max_nm=1200,
         rated_input_speed_rpm=3000, max_input_speed_rpm=6000, weight_kg=33,
         backlash_p0_arcmin=3, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.73")),
    dict(series="AB", model_name="AB220", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=48, input_bore_optional_mm=None,
         rated_torque_min_nm=1140, rated_torque_max_nm=2000,
         rated_input_speed_rpm=2000, max_input_speed_rpm=4000, weight_kg=60,
         backlash_p0_arcmin=3, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.73")),

    # ── ABR 1단 (i=3~20), p.75 ──
    dict(series="ABR", model_name="ABR042", stage=1, ratio_list=_ABR1_RATIOS, ratio_range_label="3~20",
         input_bore_std_mm=11, input_bore_optional_mm=12,
         rated_torque_min_nm=9, rated_torque_max_nm=19,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=0.9,
         backlash_p0_arcmin=None, backlash_p1_arcmin=4, backlash_p2_arcmin=6,
         backlash_note="P0급 제작안됨", source_note=_ABR_SOURCE.format(dim_page="p.75")),
    dict(series="ABR", model_name="ABR060", stage=1, ratio_list=_ABR1_RATIOS, ratio_range_label="3~20",
         input_bore_std_mm=14, input_bore_optional_mm=16,
         rated_torque_min_nm=36, rated_torque_max_nm=60,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=2.1,
         backlash_p0_arcmin=None, backlash_p1_arcmin=4, backlash_p2_arcmin=6,
         backlash_note="P0급 제작안됨", source_note=_ABR_SOURCE.format(dim_page="p.75")),
    dict(series="ABR", model_name="ABR090", stage=1, ratio_list=_ABR1_RATIOS, ratio_range_label="3~20",
         input_bore_std_mm=19, input_bore_optional_mm=24,
         rated_torque_min_nm=90, rated_torque_max_nm=150,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=6.4,
         backlash_p0_arcmin=2, backlash_p1_arcmin=4, backlash_p2_arcmin=6,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.75")),
    dict(series="ABR", model_name="ABR115", stage=1, ratio_list=_ABR1_RATIOS, ratio_range_label="3~20",
         input_bore_std_mm=32, input_bore_optional_mm=None,
         rated_torque_min_nm=195, rated_torque_max_nm=325,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=13,
         backlash_p0_arcmin=2, backlash_p1_arcmin=4, backlash_p2_arcmin=6,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.75")),
    dict(series="ABR", model_name="ABR142", stage=1, ratio_list=_ABR1_RATIOS, ratio_range_label="3~20",
         input_bore_std_mm=38, input_bore_optional_mm=None,
         rated_torque_min_nm=342, rated_torque_max_nm=650,
         rated_input_speed_rpm=3000, max_input_speed_rpm=6000, weight_kg=24.5,
         backlash_p0_arcmin=2, backlash_p1_arcmin=4, backlash_p2_arcmin=6,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.75")),
    dict(series="ABR", model_name="ABR180", stage=1, ratio_list=_ABR1_RATIOS, ratio_range_label="3~20",
         input_bore_std_mm=48, input_bore_optional_mm=None,
         rated_torque_min_nm=588, rated_torque_max_nm=1200,
         rated_input_speed_rpm=3000, max_input_speed_rpm=6000, weight_kg=51,
         backlash_p0_arcmin=2, backlash_p1_arcmin=4, backlash_p2_arcmin=6,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.75")),
    dict(series="ABR", model_name="ABR220", stage=1, ratio_list=_ABR1_RATIOS, ratio_range_label="3~20",
         input_bore_std_mm=55, input_bore_optional_mm=None,
         rated_torque_min_nm=1140, rated_torque_max_nm=2000,
         rated_input_speed_rpm=2000, max_input_speed_rpm=4000, weight_kg=83,
         backlash_p0_arcmin=2, backlash_p1_arcmin=4, backlash_p2_arcmin=6,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.75")),

    # ── ABR 2단 (i=25~200), p.76 ──
    dict(series="ABR", model_name="ABR042", stage=2, ratio_list=_ABR2_RATIOS, ratio_range_label="25~200",
         input_bore_std_mm=11, input_bore_optional_mm=12,
         rated_torque_min_nm=14, rated_torque_max_nm=20,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=1.2,
         backlash_p0_arcmin=None, backlash_p1_arcmin=7, backlash_p2_arcmin=9,
         backlash_note="P0급 제작안됨", source_note=_ABR_SOURCE.format(dim_page="p.76")),
    dict(series="ABR", model_name="ABR060", stage=2, ratio_list=_ABR2_RATIOS, ratio_range_label="25~200",
         input_bore_std_mm=11, input_bore_optional_mm=12,
         rated_torque_min_nm=40, rated_torque_max_nm=60,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=1.5,
         backlash_p0_arcmin=None, backlash_p1_arcmin=7, backlash_p2_arcmin=9,
         backlash_note="P0급 제작안됨", source_note=_ABR_SOURCE.format(dim_page="p.76")),
    dict(series="ABR", model_name="ABR090", stage=2, ratio_list=_ABR2_RATIOS, ratio_range_label="25~200",
         input_bore_std_mm=14, input_bore_optional_mm=16,
         rated_torque_min_nm=100, rated_torque_max_nm=150,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=7.8,
         backlash_p0_arcmin=4, backlash_p1_arcmin=7, backlash_p2_arcmin=9,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.76")),
    dict(series="ABR", model_name="ABR115", stage=2, ratio_list=_ABR2_RATIOS, ratio_range_label="25~200",
         input_bore_std_mm=19, input_bore_optional_mm=24,
         rated_torque_min_nm=230, rated_torque_max_nm=325,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=14.2,
         backlash_p0_arcmin=4, backlash_p1_arcmin=7, backlash_p2_arcmin=9,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.76")),
    dict(series="ABR", model_name="ABR142", stage=2, ratio_list=_ABR2_RATIOS, ratio_range_label="25~200",
         input_bore_std_mm=32, input_bore_optional_mm=None,
         rated_torque_min_nm=450, rated_torque_max_nm=650,
         rated_input_speed_rpm=3000, max_input_speed_rpm=6000, weight_kg=27.5,
         backlash_p0_arcmin=4, backlash_p1_arcmin=7, backlash_p2_arcmin=9,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.76")),
    dict(series="ABR", model_name="ABR180", stage=2, ratio_list=_ABR2_RATIOS, ratio_range_label="25~200",
         input_bore_std_mm=38, input_bore_optional_mm=None,
         rated_torque_min_nm=900, rated_torque_max_nm=1200,
         rated_input_speed_rpm=3000, max_input_speed_rpm=6000, weight_kg=54,
         backlash_p0_arcmin=4, backlash_p1_arcmin=7, backlash_p2_arcmin=9,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.76")),
    dict(series="ABR", model_name="ABR220", stage=2, ratio_list=_ABR2_RATIOS, ratio_range_label="25~200",
         input_bore_std_mm=48, input_bore_optional_mm=None,
         rated_torque_min_nm=1500, rated_torque_max_nm=2000,
         rated_input_speed_rpm=2000, max_input_speed_rpm=4000, weight_kg=95,
         backlash_p0_arcmin=4, backlash_p1_arcmin=7, backlash_p2_arcmin=9,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.76")),
]


async def main():
    assert len(REDUCER_ROWS) == 30, f"행 개수가 30이 아님: {len(REDUCER_ROWS)}"

    async with async_session() as db:
        for row in REDUCER_ROWS:
            db.add(Reducer(**row))
        await db.commit()

    print(f"완료 - Reducer {len(REDUCER_ROWS)}행 등록 (AB 16행 + ABR 14행)")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 실행**

Run:
```bash
cd "C:/nextapp/hdauto-chatbot" && PYTHONIOENCODING=utf-8 "C:/Users/last_/AppData/Local/Programs/Python/Python313/python.exe" register_apex_reducer.py
```
Expected: `완료 - Reducer 30행 등록 (AB 16행 + ABR 14행)`

- [ ] **Step 3: DB 반영 검증**

Run:
```bash
cd "C:/nextapp/hdauto-chatbot" && PYTHONIOENCODING=utf-8 "C:/Users/last_/AppData/Local/Programs/Python/Python313/python.exe" -c "
import asyncio
from sqlalchemy import select, func
from app.db.database import async_session
from app.db.models import Reducer

async def main():
    async with async_session() as db:
        total = (await db.execute(select(func.count()).select_from(Reducer))).scalar()
        print('total rows:', total)
        ab060a = (await db.execute(select(Reducer).where(Reducer.model_name == 'AB060A'))).scalars().first()
        print('AB060A stage:', ab060a.stage, 'bore std/opt:', ab060a.input_bore_std_mm, ab060a.input_bore_optional_mm)

asyncio.run(main())
"
```
Expected: `total rows: 30` and `AB060A stage: 2 bore std/opt: 14.0 16.0`

- [ ] **Step 4: 커밋**

```bash
cd "C:/nextapp/hdauto-chatbot" && git add register_apex_reducer.py && git commit -m "$(cat <<'EOF'
feat: APEX AB/ABR 감속기 카탈로그 30행 등록

docs/datasheets/apex_reduer/apex감속기 06AB+Series.pdf에서 pdfplumber로
추출 + 애매한 셀(AB060A/AB090A 2단 입력홀 등)은 페이지 이미지 크롭으로
육안 확인한 확정값. C1~C10(모터 장착 어댑터)은 카탈로그 각주에 따라
제외 — 매칭 로직이 이 값을 쓰지 않음.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 매칭 헬퍼 함수 추가 (`servo_spec_search.py`)

**Files:**
- Modify: `app/services/servo_spec_search.py:7` (import)
- Modify: `app/services/servo_spec_search.py:11-19` (상수 추가)
- Modify: `app/services/servo_spec_search.py:231-238` 다음 (`_all_reducer_rows` 등 추가)

**Interfaces:**
- Consumes: `app.db.models.Reducer` (Task 1/2)
- Produces:
  - `_REDUCER_ADAPTER_DISCLAIMER: str` — Task 4가 사용
  - `async def _all_reducer_rows(db: AsyncSession) -> list[Reducer]` — Task 4/5가 사용
  - `def _match_reducers_by_bore(shaft_mm: float, reducer_rows: list[Reducer]) -> list[tuple[Reducer, str]]` — Task 4/5가 사용. 반환 튜플의 두번째 값은 `"표준"` 또는 `"옵션 주문 필요"`.
  - `def _format_reducer_matches(shaft_mm: float, matches: list[tuple[Reducer, str]]) -> str` — Task 4가 사용

- [ ] **Step 1: import에 `Reducer` 추가**

`app/services/servo_spec_search.py:7`:
```python
from app.db.models import Product, Specification, Replacement, ProductStatus
```
을
```python
from app.db.models import Product, Specification, Replacement, ProductStatus, Reducer
```
로 교체.

- [ ] **Step 2: `_REDUCER_ADAPTER_DISCLAIMER` 상수 추가**

`app/services/servo_spec_search.py:11-19`(`_DIMENSION_DISCLAIMER` + `_with_dimension_disclaimer`) 바로 다음, `async def find_servo_by_capacity` 앞에 삽입:

```python
# ─── 감속기 자동매칭 결과에 항상 첨부하는 어댑터 확인 문구 (치수 disclaimer와 별개) ───
_REDUCER_ADAPTER_DISCLAIMER = (
    "\n\n🔩 정확한 모터 장착 어댑터(C1~C10)는 APEX 측 확인이 필요합니다."
)
```

- [ ] **Step 3: `_all_reducer_rows`, `_match_reducers_by_bore`, `_format_reducer_matches` 추가**

`app/services/servo_spec_search.py:238`(`_all_servo_rows` 함수 끝) 다음, `def _format_motor_spec_block` 앞에 삽입:

```python
async def _all_reducer_rows(db: AsyncSession) -> list[Reducer]:
    result = await db.execute(select(Reducer))
    return list(result.scalars().all())


def _match_reducers_by_bore(shaft_mm: float, reducer_rows: list[Reducer]) -> list[tuple[Reducer, str]]:
    """축경(mm) 기준으로 호환 가능한 감속기를 찾는다.

    단순히 '축경 <= 입력홀'인 행을 전부 반환하지 않는다 — 그러면 14mm 모터에도
    입력홀 <=48mm인 AB220(2000Nm급 대형 감속기)까지 걸려 숫자로는 맞지만 실제로는
    터무니없는 추천이 된다. 대신 이 축경을 수용하는 행들 중 '가장 작은 입력홀 등급'에
    해당하는 행만 반환한다 (동급 여러 시리즈/단수가 동점일 수 있음).
    """
    fits: list[tuple[Reducer, float, str]] = []
    for r in reducer_rows:
        if r.input_bore_std_mm is not None and shaft_mm <= r.input_bore_std_mm:
            fits.append((r, r.input_bore_std_mm, "표준"))
        elif r.input_bore_optional_mm is not None and shaft_mm <= r.input_bore_optional_mm:
            fits.append((r, r.input_bore_optional_mm, "옵션 주문 필요"))

    if not fits:
        return []

    min_bore = min(bore for _, bore, _ in fits)
    matches = [(r, grade) for r, bore, grade in fits if bore == min_bore]
    matches.sort(key=lambda m: (m[0].series, m[0].model_name, m[0].stage))
    return matches


def _format_reducer_matches(shaft_mm: float, matches: list[tuple[Reducer, str]]) -> str:
    lines = [f"축경 {shaft_mm:g}mm 기준 호환 가능 감속기 {len(matches)}건:"]
    for r, grade in matches:
        bore_note = f"≤{r.input_bore_std_mm:g}mm"
        if r.input_bore_optional_mm is not None:
            bore_note += f"(옵션 ≤{r.input_bore_optional_mm:g}mm)"
        lines.append(f"- {r.model_name} ({r.stage}단, 입력홀 {bore_note}) — {grade}")
    return "\n".join(lines)
```

- [ ] **Step 4: 단위 동작 확인 (스크립트로 직접 호출)**

Task 2에서 이미 30행이 DB에 있으므로 바로 확인 가능:

Run:
```bash
cd "C:/nextapp/hdauto-chatbot" && PYTHONIOENCODING=utf-8 "C:/Users/last_/AppData/Local/Programs/Python/Python313/python.exe" -c "
import asyncio
from app.db.database import async_session
from app.services.servo_spec_search import _all_reducer_rows, _match_reducers_by_bore, _format_reducer_matches

async def main():
    async with async_session() as db:
        rows = await _all_reducer_rows(db)
        print('reducer rows:', len(rows))

        m14 = _match_reducers_by_bore(14.0, rows)
        print(_format_reducer_matches(14.0, m14))
        print()

        m100 = _match_reducers_by_bore(100.0, rows)
        print('100mm matches:', m100)

asyncio.run(main())
"
```
Expected: `reducer rows: 30`, 14mm 매칭 결과에 `AB060 (1단, 입력홀 ≤14mm...)`, `AB060A (2단...)`, `AB090 (2단...)`, `ABR060 (1단...)`, `ABR090 (2단...)` 5건이 나오고, `100mm matches: []`.
(주의: 최초 계획 작성 시 손으로 세다가 ABR090(2단, 표준 14mm)을 빠뜨려 "4건"으로
잘못 적었던 걸 실제 DB 조회로 정정 — 코드가 아니라 이 기대값 텍스트가 틀렸던 것.)

- [ ] **Step 5: 커밋**

```bash
cd "C:/nextapp/hdauto-chatbot" && git add app/services/servo_spec_search.py && git commit -m "$(cat <<'EOF'
feat: 감속기 축경 기반 자동매칭 헬퍼 추가

_match_reducers_by_bore는 축경을 수용하는 가장 작은 입력홀 등급의
감속기만 반환 (단순 <= 비교시 대형 감속기까지 걸리는 문제 방지).
아직 호출부(_format_motor_spec_block)에는 연결 안 함 — 다음 커밋.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: 자동매칭을 `_format_motor_spec_block`/`find_reducer_compat`에 연결

**Files:**
- Modify: `app/services/servo_spec_search.py:241` (`_format_motor_spec_block` 시그니처)
- Modify: `app/services/servo_spec_search.py:282-294` (`reducers` 처리 분기)
- Modify: `app/services/servo_spec_search.py:311-344` (`find_reducer_compat`)

**Interfaces:**
- Consumes: `_all_reducer_rows`, `_match_reducers_by_bore`, `_format_reducer_matches`, `_REDUCER_ADAPTER_DISCLAIMER` (Task 3)
- Produces: `_format_motor_spec_block(motor_key: str, motor_data: dict, reducer_rows: list[Reducer]) -> str` (시그니처 변경 — 기존 2-인자에서 3-인자로). `find_reducer_compat`의 외부 시그니처(`(model_name, db) -> str | None`)는 변경 없음 — Task 5가 그대로 호출한다.

- [ ] **Step 1: `_format_motor_spec_block` 시그니처 변경**

`app/services/servo_spec_search.py:241`:
```python
def _format_motor_spec_block(motor_key: str, motor_data: dict) -> str:
```
을
```python
def _format_motor_spec_block(motor_key: str, motor_data: dict, reducer_rows: list[Reducer]) -> str:
```
로 교체.

- [ ] **Step 2: `reducers` 처리 분기를 3단계로 확장**

`app/services/servo_spec_search.py:282-294`:
```python
    reducers = motor_data.get("reducers") or []
    if reducers:
        reducer_lines = []
        for r in reducers:
            parts = [r.get("model", "-")]
            if r.get("reduction_ratio"):
                parts.append(f"감속비 {r['reduction_ratio']}")
            if r.get("coupling_note"):
                parts.append(r["coupling_note"])
            reducer_lines.append(" · ".join(parts))
        lines.append("결합 가능 감속기:\n" + "\n".join(f"  - {rl}" for rl in reducer_lines))
    else:
        lines.append("결합 가능 감속기: 감속기 호환 정보 미등록")
```
을
```python
    reducers = motor_data.get("reducers") or []
    if reducers:
        reducer_lines = []
        for r in reducers:
            parts = [r.get("model", "-")]
            if r.get("reduction_ratio"):
                parts.append(f"감속비 {r['reduction_ratio']}")
            if r.get("coupling_note"):
                parts.append(r["coupling_note"])
            reducer_lines.append(" · ".join(parts))
        lines.append("결합 가능 감속기:\n" + "\n".join(f"  - {rl}" for rl in reducer_lines))
    else:
        shaft_mm = dims.get("shaft_diameter_mm")
        if shaft_mm is None:
            lines.append("결합 가능 감속기: 감속기 호환 정보 미등록")
        else:
            auto_matches = _match_reducers_by_bore(shaft_mm, reducer_rows)
            if auto_matches:
                lines.append(
                    _format_reducer_matches(shaft_mm, auto_matches) + _REDUCER_ADAPTER_DISCLAIMER
                )
            else:
                lines.append(
                    "결합 가능 감속기: AB/ABR 라인업 내 호환 모델 없음 "
                    "(다른 감속기 시리즈 또는 커스텀 확인 필요)"
                )
```
로 교체. (`dims`는 이 함수 앞부분의 `dims = motor_data.get("dimensions") or {}`에서 이미 정의돼 있음 — 그대로 재사용.)

- [ ] **Step 3: `find_reducer_compat`이 `reducer_rows`를 가져와 전달하도록 수정**

`app/services/servo_spec_search.py:311-344`:
```python
async def find_reducer_compat(model_name: str, db: AsyncSession) -> str | None:
    """드라이브 모델명 또는 모터 모델명으로 등록된 감속기 결합사양(motor_specs)을 조회.

    - model_name이 드라이브 자체와 매칭되면 그 드라이브의 motor_specs 전체를 반환.
    - 아니면 model_name을 모터명으로 간주해 모든 드라이브의 motor_specs 키를 매칭.
    - motor_specs 데이터가 없으면(=아직 검증된 치수/감속기 정보가 없는 모터) None 반환
      → 호출부가 기존 폴백(일반 스펙 조회 등)으로 넘어가게 함.
    """
    rows = await _all_servo_rows(db)
    key = model_name.strip().lower()

    # 1) 드라이브 자체 모델명/시리즈로 매칭
    for p, s in rows:
        if key in p.model_name.lower() or (p.series and key in p.series.lower()):
            motor_specs = (s.extra_specs or {}).get("motor_specs") or {}
            if motor_specs:
                blocks = [_format_motor_spec_block(m, d) for m, d in motor_specs.items()]
                header = f"**{p.manufacturer} {p.model_name}** 호환 모터 결합사양:"
                return _with_dimension_disclaimer(f"{header}\n\n" + "\n\n".join(blocks))

    # 2) 모터명으로 매칭 (여러 드라이브에 걸쳐 등록돼 있을 수 있음 — 모두 수집)
    matched_blocks = []
    for p, s in rows:
        motor_specs = (s.extra_specs or {}).get("motor_specs") or {}
        for motor_key, motor_data in motor_specs.items():
            if key in motor_key.lower() or motor_key.lower() in key:
                block = _format_motor_spec_block(motor_key, motor_data)
                matched_blocks.append(f"(호환 드라이브: {p.manufacturer} {p.model_name})\n{block}")

    if not matched_blocks:
        return None

    header = f"**{model_name}** 결합사양:"
    return _with_dimension_disclaimer(f"{header}\n\n" + "\n\n".join(matched_blocks))
```
을
```python
async def find_reducer_compat(model_name: str, db: AsyncSession) -> str | None:
    """드라이브 모델명 또는 모터 모델명으로 등록된 감속기 결합사양(motor_specs)을 조회.

    - model_name이 드라이브 자체와 매칭되면 그 드라이브의 motor_specs 전체를 반환.
    - 아니면 model_name을 모터명으로 간주해 모든 드라이브의 motor_specs 키를 매칭.
    - motor_specs 데이터가 없으면(=아직 검증된 치수/감속기 정보가 없는 모터) None 반환
      → 호출부가 기존 폴백(일반 스펙 조회 등)으로 넘어가게 함.
    """
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
    for p, s in rows:
        motor_specs = (s.extra_specs or {}).get("motor_specs") or {}
        for motor_key, motor_data in motor_specs.items():
            if key in motor_key.lower() or motor_key.lower() in key:
                block = _format_motor_spec_block(motor_key, motor_data, reducer_rows)
                matched_blocks.append(f"(호환 드라이브: {p.manufacturer} {p.model_name})\n{block}")

    if not matched_blocks:
        return None

    header = f"**{model_name}** 결합사양:"
    return _with_dimension_disclaimer(f"{header}\n\n" + "\n\n".join(matched_blocks))
```
로 교체.

- [ ] **Step 4: 실제 챗봇 흐름으로 동작 확인**

Run:
```bash
cd "C:/nextapp/hdauto-chatbot" && PYTHONIOENCODING=utf-8 "C:/Users/last_/AppData/Local/Programs/Python/Python313/python.exe" -c "
import asyncio
from app.db.database import async_session
from app.core.intent import classify_intent
from app.api.chatbot import _route

async def main():
    async with async_session() as db:
        try:
            intent_result = classify_intent('HC-KFS43 서보모터 감속기 부착하고 싶은데 사이즈 알려줘')
            reply, source = await _route(intent_result, 'HC-KFS43 서보모터 감속기 부착하고 싶은데 사이즈 알려줘', db)
            print(reply)
            assert '감속기 호환 정보 미등록' not in reply
            assert 'AB060' in reply
            assert 'APEX 측 확인이 필요합니다' in reply
            print('PASS')
        finally:
            await db.rollback()

asyncio.run(main())
"
```
Expected: 응답에 `AB060`(1단)/`AB060A`(2단)/`AB090`(2단)/`ABR060`(1단)/`ABR090`(2단) 매칭 결과(5건)와 `🔩 정확한 모터 장착 어댑터(C1~C10)는 APEX 측 확인이 필요합니다.` 문구, 마지막 줄 `PASS`.

- [ ] **Step 5: 커밋**

```bash
cd "C:/nextapp/hdauto-chatbot" && git add app/services/servo_spec_search.py && git commit -m "$(cat <<'EOF'
feat: 감속기 결합사양 조회에 축경 기반 자동매칭 연결

curated motor_specs.reducers가 비어있을 때: shaft_diameter_mm이 있으면
자동매칭(있으면 결과+어댑터확인, 없으면 '라인업 내 호환 모델 없음'),
없으면 기존 '감속기 호환 정보 미등록' 유지 — 세 상태를 명확히 구분.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `test_hc_kfs_chatbot_flow.py` 확장 + 전체 검증

**Files:**
- Modify: `test_hc_kfs_chatbot_flow.py` (전체 재작성)

**Interfaces:**
- Consumes: `app.services.servo_spec_search._REDUCER_ADAPTER_DISCLAIMER`, `_match_reducers_by_bore`, `_format_motor_spec_block`, `_all_reducer_rows` (Task 3/4), `app.api.chatbot._route` (기존)

- [ ] **Step 1: `test_hc_kfs_chatbot_flow.py` 전체를 아래 내용으로 교체**

```python
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
```

- [ ] **Step 2: 실행**

Run:
```bash
cd "C:/nextapp/hdauto-chatbot" && PYTHONIOENCODING=utf-8 "C:/Users/last_/AppData/Local/Programs/Python/Python313/python.exe" test_hc_kfs_chatbot_flow.py
```
Expected: 5개 블록 전부 `PASS` 출력, 마지막 줄 `모든 테스트 통과.`, 종료 코드 0 (`AssertionError` 없음).

- [ ] **Step 3: 실패 시 대응**

만약 4개 케이스 중 하나라도 예상과 다른 매칭 결과가 나오면 (예: 등록 단계에서 오타로
다른 축경이 들어갔거나, `_match_reducers_by_bore`의 최소 등급 계산이 잘못됨) — 추측으로
assertion을 고치지 말고, 먼저 `docs/superpowers/specs/2026-07-11-apex-reducer-matching-design.md`의
"데이터 확정값" 표와 실제 DB에 들어간 값을 대조해서 어느 쪽이 틀렸는지 확인한다.

- [ ] **Step 4: 커밋**

```bash
cd "C:/nextapp/hdauto-chatbot" && git add test_hc_kfs_chatbot_flow.py && git commit -m "$(cat <<'EOF'
test: 감속기 자동매칭 검증 - 4개 실기 케이스 + 매칭없음 단위 테스트

HC-KFS053/13/23/43 전부 실제 APEX 카탈로그와 매칭되는 것으로 확인돼
'미등록' 대신 매칭 결과+어댑터 확인 문구를 검증하도록 변경. 등록된
모터로는 도달 불가능한 '매칭 없음' 분기는 _match_reducers_by_bore/
_format_motor_spec_block을 가상 축경(100mm)으로 직접 호출해 별도 검증.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review 결과

**스펙 커버리지:**
- Reducer 테이블 신설 → Task 1
- register_apex_reducer.py + C1~C10 제외 주석 → Task 2
- 축경 기반 자동매칭(_match_reducers_by_bore) → Task 3
- 어댑터 확인 문구 자동 첨부 → Task 3(정의)/Task 4(연결)
- _format_motor_spec_block 3단계 분기(curated/자동매칭/미등록) → Task 4
- 4개 케이스 확장 + "매칭 없음" 문구 실제 확인(5번째 단위 테스트) → Task 5

**타입 일관성:** `_format_motor_spec_block(motor_key, motor_data, reducer_rows)` 시그니처가
Task 4에서 정의되고 Task 4의 `find_reducer_compat` 호출부(2곳) + Task 5의 단위 테스트에서
동일하게 3-인자로 쓰임. `_match_reducers_by_bore(shaft_mm, reducer_rows) -> list[tuple[Reducer, str]]`
반환 타입이 `_format_reducer_matches`와 Task 5 단위 테스트 모두에서 동일하게 소비됨.

**플레이스홀더 스캔:** 없음 — 모든 코드 블록이 완성된 실행 가능 코드, 데이터 30행 전부 확정값.
