# APEX AB/ABR 감속기 카탈로그 등록 + 축경 기반 자동매칭 설계

날짜: 2026-07-11
관련 커밋 컨텍스트: HC-KFS 서보모터 치수/감속기 결합 기능 (register_hc_kfs_servo.py, servo_spec_search.py, test_hc_kfs_chatbot_flow.py) 후속.

## 배경

`servo_spec_search.py`의 `find_reducer_compat`는 각 모터의 `motor_specs[모터명]["reducers"]`에
수기로 등록된(curated) 감속기 목록만 보여준다. 현재는 전부 빈 리스트라 항상
"감속기 호환 정보 미등록"이 나온다.

이번 작업은 APEX AB/ABR 시리즈 감속기 카탈로그(`docs/datasheets/apex_reduer/apex감속기 06AB+Series.pdf`,
pdfplumber로 텍스트 추출 가능)를 DB에 등록하고, 모터의 축경(shaft_diameter_mm)을 기준으로
호환 가능한 감속기를 **자동으로** 찾아주는 매칭 로직을 추가한다. curated 리스트가 있으면
그게 우선이고, 없을 때만 자동매칭으로 폴백한다.

## PDF 데이터 조사 결과 (12페이지 전체 확인)

- **AB 시리즈 9개 모델**: AB042, AB060, AB060A, AB090, AB090A, AB115, AB142, AB180, AB220.
  AB060A/AB090A는 **2단 감속(i=15~100) 전용** — 1단 버전 없음.
- **ABR 시리즈 7개 모델**: ABR042~ABR220. A베리언트 없음.
- **정격 출력토크는 (모델 × 단수 × 감속비) 조합마다 다르다** — 모델당 단일값이 아님.
- **입력홀(축경 허용) 규격도 1단/2단마다 다르다** (예: AB090 1단 ≤19/≤24mm, AB090 2단은 표 추출이 깨져 확인 필요).
- 카탈로그 각주: "C1~C10은 적용모터에 따라 다릅니다. 당사 홈페이지... Design Tool을 이용하여 확인" —
  즉 모터 장착 어댑터 치수는 이 카탈로그만으론 확정 불가, 항상 APEX 확인이 필요한 항목.
- 일부 셀(특히 AB060A/AB090A의 2단 입력홀, page 4 `C3` 행)은 pdfplumber 표 추출이
  줄바꿈 때문에 열이 밀려서 깨졌다. **구현 단계에서 해당 페이지를 이미지로 렌더링 후
  크롭하여 육안 확인**하고 값을 확정한다 (HC-KFS 작업 때 `_pages_tmp/*.png`로 검증한 것과 동일 절차).
  애매한 값을 추측해서 넣지 않는다.

## 결정된 설계

### 1. 데이터 모델 — 새 `Reducer` 테이블 (`app/db/models.py`)

Product/Inventory와 무관한 **순수 참고 데이터**(재고관리 대상 아님).

```python
class Reducer(Base):
    __tablename__ = "reducers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    series = Column(String(10), nullable=False, index=True)       # "AB" | "ABR"
    model_name = Column(String(20), nullable=False, index=True)   # "AB042", "AB060A", ...
    stage = Column(Integer, nullable=False)                       # 1 | 2
    ratio_list = Column(JSON)              # [3,4,5,6,7,8,9,10] 등 선택 가능 감속비
    ratio_range_label = Column(String(20)) # "3~10" 표시용

    input_bore_std_mm = Column(Float)       # 표준 입력홀 최대 허용 축경
    input_bore_optional_mm = Column(Float)  # 옵션 주문시 최대 허용 축경 (nullable)

    rated_torque_min_nm = Column(Float)     # 감속비 구간 내 정격토크 범위
    rated_torque_max_nm = Column(Float)

    rated_input_speed_rpm = Column(Integer)
    max_input_speed_rpm = Column(Integer)

    weight_kg = Column(Float)

    backlash_p0_arcmin = Column(Float)   # nullable — 미생산 등급이면 None
    backlash_p1_arcmin = Column(Float)
    backlash_p2_arcmin = Column(Float)
    backlash_note = Column(String(200))  # "AB042/AB060 1,2단, AB090 2단 P0급 제작안됨" 등 각주 보존

    source_note = Column(String(200))    # 출처 페이지 (예: "apex감속기 06AB+Series.pdf p.72")
```

C1~C10(모터 장착 어댑터 치수)은 **컬럼 자체를 두지 않는다** — 매칭 로직이 애초에 이 값을 쓰지 않음.
대신 `register_apex_reducer.py` 상단 주석에 "적용 모터에 따라 다름, 자동 매칭 대상 아님,
APEX Design Tool 확인 필요"를 명시해 왜 없는지 남긴다.

`unique constraint` 없음 — `(model_name, stage)` 조합이 사실상의 자연키지만, 조회는 항상
`series`+`model_name`+`stage` 조합으로 필터링하므로 굳이 강제하지 않는다 (기존 프로젝트
패턴상 이 정도 유연성이 register 스크립트 재실행 시 유리).

### 2. `register_apex_reducer.py`

`register_hc_kfs_servo.py`와 동일한 패턴 (asyncio + async_session, 상단에 데이터 출처/제외 항목 주석).

- AB: 1단 7행 + 2단 9행 = 16행
- ABR: 1단 7행 + 2단 7행 = 14행
- 총 30행 INSERT

값 확정 전 애매한 셀은 페이지 이미지 크롭으로 재확인 (특히 AB060A/AB090A 2단 입력홀).

### 3. 매칭 로직 확장 (`app/services/servo_spec_search.py`)

**신규 헬퍼:**
- `_all_reducer_rows(db) -> list[Reducer]`: Reducer 테이블 전체 조회, 요청 컨텍스트 내 1회만.
- `_match_reducers_by_bore(shaft_mm, reducer_rows) -> list[tuple[Reducer, str]]`:
  **단순히 "축경 ≤ 입력홀"인 행을 전부 반환하지 않는다** — 그렇게 하면 14mm 모터에도
  입력홀 ≤48mm인 AB220(2000Nm급 대형 감속기)까지 걸려버려 숫자로는 맞지만 실제로는
  터무니없는 추천이 된다. 대신:
  1. 각 행에 대해 "적합 입력홀 값"을 계산 (`shaft_mm ≤ input_bore_std_mm`이면 std값,
     아니면 `shaft_mm ≤ input_bore_optional_mm`이면 opt값, 둘 다 아니면 부적합).
  2. 적합한 행들 중 **가장 작은 입력홀 값(= 가장 작은 등급)**을 찾는다.
  3. 그 최소값과 일치하는 행들만 반환 — 즉 "이 축경을 수용하는 가장 작은 급"에
     해당하는 모델만 나온다 (더 큰 모델은 기계적으로도 들어가지만 추천 대상 아님).
  결과는 1단/2단 항목이 각각 별도 행이므로 라벨에 "(1단)"/"(2단)" 포함, 표준 매칭인지
  옵션 주문 매칭인지도 함께 표시.
- `_format_reducer_matches(matches) -> str`: 모델명 + 단수 + 표준/옵션 여부를 리스트로 포맷.
- `_REDUCER_ADAPTER_DISCLAIMER` 상수 (기존 `_DIMENSION_DISCLAIMER`와 별개, 항상 매칭 결과에 첨부):
  `"\n\n🔩 정확한 모터 장착 어댑터(C1~C10)는 APEX 측 확인이 필요합니다."`

**`_format_motor_spec_block` 시그니처 확장** — `reducer_rows: list[Reducer]` 파라미터 추가.
`reducers`(curated) 처리 분기를 3단계로 나눈다:

1. `motor_data["reducers"]`(curated)가 비어있지 않음 → 기존처럼 그대로 표시 (변경 없음).
2. curated가 비어있고 `dims.shaft_diameter_mm`가 있음 → `_match_reducers_by_bore` 자동매칭:
   - 매칭 있음 → 매칭 결과 + `_REDUCER_ADAPTER_DISCLAIMER` 표시.
   - 매칭 없음 → **"AB/ABR 라인업 내 호환 모델 없음 (다른 감속기 시리즈 또는 커스텀 확인 필요)"**
     (신규 문구 — "미등록"과 구분: "확인은 했으나 이 카탈로그 라인업엔 맞는 게 없다"는 의미).
3. curated가 비어있고 `dims.shaft_diameter_mm`도 없음 → 기존 **"감속기 호환 정보 미등록"** 그대로
   (애초에 축경 자체를 몰라 매칭을 시도할 수 없는 경우 — "아직 확인 안 함").

이렇게 세 가지 상태를 명확히 구분: **curated 있음 / 자동매칭됨 / 자동매칭했지만 없음 / 애초에 시도 불가**.

**`find_reducer_compat`** 양쪽 매칭 분기(드라이브명 매칭, 모터명 매칭) 모두 `_all_reducer_rows(db)`를
1회 호출해 `_format_motor_spec_block` 호출부에 전달하도록 수정.

### 4. 검증 — `test_hc_kfs_chatbot_flow.py`

기존 4개 케이스(HC-KFS053/13/23/43) 전부 자동매칭 대상으로 전환 — 이 기능은
`shaft_diameter_mm`이 있는 모터 전체에 적용되므로 "미등록" 문구를 기대하던 기존
assertion도 함께 고친다.

PDF 페이지를 이미지로 렌더링해 육안 확인한 결과(§ 데이터 확정값 참고), **8mm은 AB042
표준 입력홀(≤11mm)보다 작으므로 오히려 정상 매칭된다** — 애초에 "축경 ≤ 입력홀"이면
매칭이므로 더 작은 축경은 항상 더 쉽게 들어간다. 즉 4개 실제 모터로는 "매칭 없음"
분기가 발생하지 않는다:

- HC-KFS053, HC-KFS13 (축경 8mm): 최소 등급 AB042(1단)/AB042(2단)/ABR042(1단)/ABR042(2단)
  (모두 표준 ≤11mm급) 매칭 예상 → 매칭 결과 + 어댑터 확인 문구 assert.
- HC-KFS23, HC-KFS43 (축경 14mm): 최소 등급 AB060(1단)/AB060A(2단)/AB090(2단)/ABR060(1단)
  (모두 입력홀 14mm급) 매칭 예상 → 매칭 결과 + 어댑터 확인 문구 assert.

**5번째 케이스(신규, 단위 테스트) — "매칭 없음" 분기 검증**: 실제 등록 모터로는
이 분기를 exercise할 수 없으므로, `_match_reducers_by_bore`를 존재하지 않는
가상 축경(100mm — AB220 최대 입력홀 55mm보다도 큼)으로 직접 호출해 빈 리스트를
반환하는지 확인하고, `_format_motor_spec_block`에 curated reducers가 없고
`shaft_diameter_mm=100`인 가상 motor_data를 넣어 "AB/ABR 라인업 내 호환 모델 없음"
문구가 정확히 나오는지 확인한다 (챗봇 전체 흐름이 아닌 함수 직접 호출 — 이 분기는
실제 등록 데이터로는 도달 불가능하므로 단위 테스트로만 커버).

## 아키텍처/데이터 흐름 요약

```
register_apex_reducer.py (1회 실행)
  → Reducer 테이블 30행 INSERT

챗봇 질의 ("HC-KFS43 감속기 부착...")
  → chatbot._route → servo_spec_search.find_reducer_compat(model_name, db)
      → _all_servo_rows(db)     (기존, 모터 motor_specs 조회)
      → _all_reducer_rows(db)   (신규, Reducer 카탈로그 조회)
      → _format_motor_spec_block(motor_key, motor_data, reducer_rows)
          curated 있음? → 그대로 표시
          curated 없음 + shaft_diameter_mm 있음? → _match_reducers_by_bore
              매칭 있음 → 결과 + 어댑터 disclaimer
              매칭 없음 → "라인업 내 호환 모델 없음" 문구
          curated 없음 + shaft_diameter_mm 없음? → "감속기 호환 정보 미등록"
      → _with_dimension_disclaimer(...)  (기존 치수 disclaimer, 그대로 유지)
```

## 에러 처리 / 엣지 케이스

- Reducer 테이블이 비어있는 상태(register 스크립트 실행 전)에서 자동매칭 시도 시:
  `_all_reducer_rows`가 빈 리스트 반환 → `_match_reducers_by_bore`도 빈 리스트 →
  "라인업 내 호환 모델 없음" 문구가 나옴. 이는 register 스크립트를 아직 안 돌렸을 때도
  같은 문구가 나온다는 뜻인데, 이 프로젝트 규모상 별도의 "카탈로그 미등록"과 "매칭 없음"을
  또 나누는 건 과도한 엔지니어링으로 판단 — register 스크립트가 배포 전 1회 실행되는 걸
  전제로 한다 (기존 register_*.py들과 동일 운영 방식).
- `input_bore_optional_mm`이 없는 모델(옵션 없음)은 `None` 유지, 매칭 시 표준 범위만 비교.

## 데이터 확정값 (PDF 페이지 이미지 크롭 검증 완료)

`docs/datasheets/apex_reduer/apex감속기 06AB+Series.pdf` page 71(72,73 dim) / 74(75,76 dim)를
300dpi로 렌더링해 육안 확인. 표 추출이 깨졌던 AB060A/AB090A 2단 입력홀, ABR 2단 입력홀
셀도 이 표에 반영된 값이 최종 확정값(더 이상 애매하지 않음).

| series | model | stage | ratio_range | bore_std/opt(mm) | torque_min~max(Nm) | speed_rated/max(rpm) | weight(kg) | P0/P1/P2(arcmin) |
|---|---|---|---|---|---|---|---|---|
| AB | AB042 | 1 | 3~10 | 11/12 | 14~22 | 5000/10000 | 0.6 | -/3/5 |
| AB | AB060 | 1 | 3~10 | 14/16 | 40~60 | 5000/10000 | 1.3 | -/3/5 |
| AB | AB090 | 1 | 3~10 | 19/24 | 100~160 | 4000/8000 | 3.7 | 1/3/5 |
| AB | AB115 | 1 | 3~10 | 32/- | 230~330 | 4000/8000 | 7.8 | 1/3/5 |
| AB | AB142 | 1 | 3~10 | 38/- | 342~650 | 3000/6000 | 14.5 | 1/3/5 |
| AB | AB180 | 1 | 3~10 | 48/- | 588~1200 | 3000/6000 | 29 | 1/3/5 |
| AB | AB220 | 1 | 3~10 | 55/- | 1140~2000 | 2000/4000 | 48 | 1/3/5 |
| AB | AB042 | 2 | 15~100 | 11/12 | 14~22 | 5000/10000 | 0.8 | -/5/7 |
| AB | AB060 | 2 | 15~100 | 11/12 | 40~60 | 5000/10000 | 1.5 | -/5/7 |
| AB | AB060A | 2 | 15~100 | 14/16 | 40~60 | 5000/10000 | 1.9 | -/5/7 |
| AB | AB090 | 2 | 15~100 | 14/16 | 100~160 | 4000/8000 | 4.1 | ★/5/7 |
| AB | AB090A | 2 | 15~100 | 19/24 | 100~160 | 4000/8000 | 5.3 | -/5/7 |
| AB | AB115 | 2 | 15~100 | 19/24 | 230~330 | 4000/8000 | 9 | 3/5/7 |
| AB | AB142 | 2 | 15~100 | 32/- | 342~650 | 3000/6000 | 17.5 | 3/5/7 |
| AB | AB180 | 2 | 15~100 | 38/- | 588~1200 | 3000/6000 | 33 | 3/5/7 |
| AB | AB220 | 2 | 15~100 | 48/- | 1140~2000 | 2000/4000 | 60 | 3/5/7 |
| ABR | ABR042 | 1 | 3~20 | 11/12 | 9~19 | 5000/10000 | 0.9 | -/4/6 |
| ABR | ABR060 | 1 | 3~20 | 14/16 | 36~60 | 5000/10000 | 2.1 | -/4/6 |
| ABR | ABR090 | 1 | 3~20 | 19/24 | 90~150 | 4000/8000 | 6.4 | 2/4/6 |
| ABR | ABR115 | 1 | 3~20 | 32/- | 195~325 | 4000/8000 | 13 | 2/4/6 |
| ABR | ABR142 | 1 | 3~20 | 38/- | 342~650 | 3000/6000 | 24.5 | 2/4/6 |
| ABR | ABR180 | 1 | 3~20 | 48/- | 588~1200 | 3000/6000 | 51 | 2/4/6 |
| ABR | ABR220 | 1 | 3~20 | 55/- | 1140~2000 | 2000/4000 | 83 | 2/4/6 |
| ABR | ABR042 | 2 | 25~200 | 11/12 | 14~20 | 5000/10000 | 1.2 | -/7/9 |
| ABR | ABR060 | 2 | 25~200 | 11/12 | 40~60 | 5000/10000 | 1.5 | -/7/9 |
| ABR | ABR090 | 2 | 25~200 | 14/16 | 100~150 | 4000/8000 | 7.8 | 4/7/9 |
| ABR | ABR115 | 2 | 25~200 | 19/24 | 230~325 | 4000/8000 | 14.2 | 4/7/9 |
| ABR | ABR142 | 2 | 25~200 | 32/- | 450~650 | 3000/6000 | 27.5 | 4/7/9 |
| ABR | ABR180 | 2 | 25~200 | 38/- | 900~1200 | 3000/6000 | 54 | 4/7/9 |
| ABR | ABR220 | 2 | 25~200 | 48/- | 1500~2000 | 2000/4000 | 95 | 4/7/9 |

비고:
- `bore_opt`가 "-"인 행은 옵션 없음(`input_bore_optional_mm=None`).
- AB090(2단) P0="★" = "고객 요청 시 특별 제작 가능" (표준 미생산, 커스텀 주문 가능) — `backlash_p0_arcmin=None` +
  `backlash_note="P0급: 고객 요청 시 특별 제작 가능(★, 납기/가격 변동)"`으로 저장.
- P0="-"인 행은 `backlash_p0_arcmin=None` + `backlash_note="P0급 제작안됨"`.
- AB060A/AB090A는 카탈로그상 "Special type" 각주가 붙음(주문 시 별도 확인 필요라는 의미) —
  `source_note`에 "Special type" 명시.

## 테스트 범위

`test_hc_kfs_chatbot_flow.py` 기존 스크립트 확장만 — 새 pytest 도입 없음 (프로젝트에
테스트 프레임워크가 없다는 기존 관례 유지, CLAUDE.md 참고).
