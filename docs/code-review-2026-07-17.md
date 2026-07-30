# 코드 리뷰 리포트 — 현대자동화/현대기전사 챗봇 (2026-07-17)

## 0. 개요

- **범위**: 저장소 전체 (FastAPI 앱, DB 접근계층, Tailscale 프록시, 3단계 폴백(DB→네이버검색→CLOVA), 서보/감속기 로직, 카카오 알림, 루트 레벨 데이터 마이그레이션/매칭 스크립트)
- **브랜치/워크트리 상태**:
  - `master`와 `feature/servo-response-restructure`는 **동일 커밋(`fc483b5`)** — 병합 안 된 커밋 차이는 없음.
  - 다만 워크트리(`.claude/worktrees/feature+servo-response-restructure/`)에는 **어디에도 커밋되지 않은 미추적 스크립트**가 다수 존재함(`register_motor_dimensions.py`, `check_drive_rows.py`, `render_pdf_pages.py`, `scan_hc_lfs_pages.py`, `scan_sfs_lfs_pages.py` 등). 특히 `register_motor_dimensions.py`는 사용자가 명시적으로 언급한 검토 대상이며, 아래 4-4에서 다룸.
  - 원격에는 `origin/master`만 존재, 다른 브랜치 없음.
- **방법론**: 6개 영역(API/세션, DB/Tailscale, 3단계 폴백/인텐트, 서보·감속기·안내문구, 카카오 OAuth, 마이그레이션·매칭 스크립트)을 독립적으로 병렬 검토. 코드 수정 없음, 읽기 전용 리뷰.
- **총 발견 건수**: 높음 12건 / 중간 13건 / 낮음 11건 (하단 상세 참조)

---

## 1. 심각도 "높음" 요약

| # | 위치 | 문제 요약 |
|---|------|-----------|
| H1 | `app/api/talktalk.py:189-196` | 톡톡 채널의 관리자 명령 진입 조건이 실제 명령어 포맷과 불일치해 톡톡을 통한 대체품 매핑 등록이 사실상 작동하지 않음 |
| H2 | `app/api/admin.py`, `app/api/products.py`, `app/main.py:47-50` | 재고 수정/제품 등록 API가 인증 없이(+CORS `*`) 상시 공개되어 외부에서 재고·상품 데이터 임의 조작 가능 |
| H3 | `app/api/talktalk.py:110-117` | 현재 `.env` 설정상 톡톡 웹훅 서명 검증이 경고 로그만 남기고 통과되어 위조 요청으로 외부 API(톡톡 발송, CLOVA)가 남용될 수 있음 |
| H4 | `tailscale_proxy.py:44-51`, `app/db/database.py:7-13`, `start.sh:12-23` | Tailscale 프록시/DB 연결 어디에도 connect timeout이 없어 tailnet 인증 지연 시 앱 기동/요청이 무한 대기할 수 있음 |
| H5 | `migrate_sqlite_to_mariadb.py:87-97`, `app/db/database.py:17-19` | 프로덕션 MariaDB 스키마가 `pandas.to_sql(if_exists="replace")`로 생성되어 ORM이 선언한 FK/UNIQUE/Enum 제약이 실제로는 없을 가능성이 높고, `create_all()`은 이 드리프트를 고치지 못함 |
| H6 | `app/api/chatbot.py:109, 141` | DB에서 정상적으로 찾은 답변도 CLOVA가 생성한 자유서술 텍스트에 우연히 `"찾지 못했습니다"`가 포함되면 웹검색으로 잘못 덮어써질 수 있음(과거 실제 발생한 "웹검색 AI의 근거없는 대체품 생성" 사고와 동일 패턴이 LLM 생성 경로에 남아있음) |
| H7 | `app/api/manual.py:30`, `app/services/pdf_processor.py:197` | 매뉴얼 업로드 폼 예시가 "미쓰비시"인데 seed 데이터는 전부 "Mitsubishi"이며 중복 체크·조회 필터가 정확 일치라서, 관리자가 폼 예시를 따르면 알람코드 데이터가 두 갈래로 쪼개져 조회 시 절반이 누락됨 |
| H8 | `app/services/servo_spec_search.py:393-399, 434-441` | 드라이브 하나에 다중 모터가 등록되고 모두 축경 자동매칭에 해당하면, 드라이브명 조회 시 🔩 어댑터 안내문구가 모터 개수만큼 반복되고 ⚠️ 치수 disclaimer가 그 뒤 한 번 더 붙어 "이중 안내문구"로 노출됨 — **보고된 버그의 근본 원인** |
| H9 | `app/services/admin_notify.py:72-89, 102-107`, `app/api/chatbot.py:58-66` | 카카오 API 호출(토큰갱신/발송) 예외가 어디서도 잡히지 않아, 카카오 서버 장애 시 재고 조회 자체에 대한 고객 응답이 통째로 범용 오류 메시지로 대체됨 |
| H10 | `app/services/admin_notify.py:82-84` | 카카오 `refresh_token` 만료/무효화 시 로그 한 줄만 남기고 영구 침묵 실패 — 저장소 전체에 로그 외 알림 수단이 없어 관리자가 재고 알림 중단 사실을 알 방법이 없음 |
| H11 | `migrate_origin_product_no.py` (전체) | CLAUDE.md가 명시적으로 경고하는 폐기된 `origin_product_no` 사전매핑 방식의 원점 스크립트가 폐기 경고 없이 재실행 가능한 상태로 남아있음 — 재실행 시 "재고 있는데 없다" 버그 재현 위험 |
| H12 | `finalize_matching.py:38-42, 90-149` | `naver_commerce.py` 주석이 실증 사례로 인용하는 MR-J2S-40B 오매칭 버그를 그대로 encode한 스크립트가 경고 없이 재실행 가능 |

---

## 2. API 라우팅 및 채널 세션 (webchat `/chat` ↔ 톡톡 웹훅)

### 2-1. [H1] 톡톡 채널에서 관리자 명령이 인식되지 않음
- **위치**: `app/api/talktalk.py:189-196` (vs `app/api/chatbot.py:33`, `app/core/admin_commands.py:9-15`)
- **심각도**: 높음
- **재현 시나리오**: `.env`의 `ADMIN_COMMAND_KEY=bjh21033` 기준, 웹챗에서 `대체품등록[bjh21033]: ...`을 보내면 `chatbot.py:33`의 `is_admin_command()`(prefix `"대체품등록["` 체크)가 정상 동작한다. 그러나 톡톡 웹훅에서는 `talktalk.py:189-190`이 `message.startswith("bjh21033")`으로 검사해 절대 True가 되지 않고, 일반 문의로 취급되어 웹검색/CLOVA 폴백으로 응답됨. 톡톡을 통한 대체품 매핑 등록 기능은 현재 배포 상태에서 사실상 죽어있음.
- **권장 조치**: 톡톡 쪽도 `is_admin_command(message)`(prefix `"대체품등록["`)를 게이트로 사용하도록 통일하고, 실제 키 비교는 `handle_admin_command` 내부에만 위임.

### 2-2. [H2] `/api/admin`, `/api/products` 인증 전무 + CORS 전체 허용
- **위치**: `app/api/admin.py` 전체, `app/api/products.py` 전체, `app/main.py:47-50` (CORS `allow_origins=["*"]`)
- **심각도**: 높음
- **재현 시나리오**: 배포된 Render URL을 아는 누구나 `PUT /api/admin/inventory/{model_name}`으로 실재고를 조작하거나 `POST /api/products/import/inventory-csv`로 재고/가격을 임의 덮어쓸 수 있음. `admin_notify.py`의 저재고 알림이 이 값을 신뢰하므로 허위 품절 알림을 유발하거나 반대로 실제 품절을 은폐시킬 수 있음. 매뉴얼 업로드는 `IS_LOCAL`로 막아두면서 데이터 변조 위험이 더 큰 이 엔드포인트는 열어둔 것은 일관성 없는 위협 모델링.
- **권장 조치**: 최소한 API 키 헤더 검증 의존성을 admin/products 라우터에 공통 적용하거나 `IS_LOCAL`/별도 플래그로 게이트.

### 2-3. [H3] 톡톡 웹훅 서명 검증이 현재 설정상 사실상 비활성
- **위치**: `app/api/talktalk.py:110-117`
- **심각도**: 높음
- **재현 시나리오**: `TALKTALK_SECRET`이 빈 값이고 `DEBUG=False`(현재 `.env`와 일치)인 경우 경고 로그만 남기고 서명 검증을 건너뜀. 웹훅 URL을 아는 외부인이 요청을 위조해 POST하면 실제 톡톡 발송 API·CLOVA/웹검색 API가 소모되어 비용 발생 가능.
- **권장 조치**: `TALKTALK_SECRET`을 운영 `.env`에 반드시 채우거나, 시크릿 미설정 시 운영 모드에서는 요청을 거부(403)하도록 강화.

### 2-4. [중간] 하드코딩된 `COMPANY_PHONE` 이중 관리
- **위치**: `app/api/chatbot.py:24, 64` (vs `app/config.py:34`, `app/api/talktalk.py:167`, `app/services/location.py:18`)
- **심각도**: 중간
- **재현 시나리오**: `chatbot.py`는 오류 폴백 메시지용 전화번호를 `settings.COMPANY_PHONE`이 아닌 모듈 상수로 별도 하드코딩. 현재는 값이 우연히 같지만, 향후 `.env`에서 번호를 바꾸면 웹챗 예외 폴백 메시지만 옛 번호를 안내하는 채널 간 불일치가 조용히 발생.
- **권장 조치**: `chatbot.py`의 상수를 제거하고 `settings.COMPANY_PHONE` 참조로 통일.

### 2-5. [중간] 톡톡 웹훅 — `textContent` 타입 가정이 깨지면 방어 없이 500
- **위치**: `app/api/talktalk.py:153-154` (보호되지 않는 파싱 구간 `:125-157`)
- **심각도**: 중간
- **재현 시나리오**: `textContent`가 문자열/리스트인 위조 요청이 오면 `.get()` 호출에서 `AttributeError` → 500. 웹챗은 Pydantic 스키마 검증으로 422 처리되는 것과 대조적으로 톡톡은 무방비.
- **권장 조치**: 파싱 구간을 try/except로 감싸거나 Pydantic 모델로 웹훅 바디 검증.

### 2-6. [중간] `products.py` 단건 등록의 미보호 enum 변환
- **위치**: `app/api/products.py:14-28` (특히 `:20`)
- **심각도**: 중간
- **재현 시나리오**: `POST /api/products/`에 잘못된 status 문자열을 보내면 `ValueError`가 그대로 올라가 500. CSV 일괄등록은 동일 문제를 이미 방어했는데 단건 등록만 누락.
- **권장 조치**: try/except로 감싸 422로 유효 목록 안내.

### 2-7. [낮음] `admin.py`의 `trigger_stock_alert`가 아무 것도 안 하면서 "sent" 반환
- **위치**: `app/api/admin.py:51-60`
- **심각도**: 낮음
- **재현 시나리오**: `pass`만 있고 실제 슬랙 전송 로직 없이 `{"status": "sent", ...}` 반환 — 호출자가 알림이 갔다고 오인.
- **권장 조치**: 미사용 엔드포인트면 제거, 사용 중이면 실제 로직 구현.

### 2-8. [낮음] 관리자 키 비교가 상수시간 비교가 아님
- **위치**: `app/core/admin_commands.py:29`
- **심각도**: 낮음
- **재현 시나리오**: 일반 문자열 `!=` 비교라 타이밍 사이드채널 이론적 노출(톡톡 서명검증은 `hmac.compare_digest` 사용과 대조).
- **권장 조치**: `hmac.compare_digest`로 교체.

---

## 3. DB 접근 계층 & Tailscale SOCKS5 프록시

### 3-1. [H4] Connect timeout 전무 — 무한 대기 위험
- **위치**: `tailscale_proxy.py:44-51`, `app/db/database.py:7-13`, `start.sh:12-23`, `app/main.py:23-24`
- **심각도**: 높음
- **재현 시나리오**: Render 재기동 시 `tailscaled`는 SOCKS5 서버를 바로 띄우지만 `tailscale up` 인증은 지연될 수 있고, 실패해도 `|| echo ...`로 스크립트는 계속 진행됨. tailnet 라우팅이 준비 안 된 상태에서 `proxy.connect()`(타임아웃 미설정)가 영원히 리턴하지 않으면, 이를 기다리는 `init_db()`도 무한 대기해 FastAPI가 기동조차 못 함. 런타임 중 요청도 동일하게 응답 없이 걸릴 수 있음.
- **권장 조치**: `proxy.connect()`를 `asyncio.wait_for`로 감싸고, asyncmy `connect_args={"connect_timeout": N}` 추가, `start.sh`에서 tailnet 연결 상태를 폴링, `init_db()`에 재시도 로직 도입.

### 3-2. [H5] 프로덕션 스키마가 pandas 자동추론으로 생성 — ORM 제약 미반영 가능성
- **위치**: `migrate_sqlite_to_mariadb.py:87-97`, `app/db/database.py:17-19`, `app/db/models.py` 전반
- **심각도**: 높음
- **재현 시나리오**: 최초 NAS MariaDB 반입 스크립트가 `df.to_sql(if_exists="replace")`로 테이블을 생성해 FK/UNIQUE/Enum/네이티브 JSON이 실제로 없을 수 있음. `init_db()`의 `create_all()`은 기존 테이블을 건드리지 않으므로 이 드리프트가 영구화됨. `Product.model_name` UNIQUE 미보장, FK 무결성이 애플리케이션 코드에만 의존. Alembic 등 마이그레이션 도구도 없어 드리프트 감지 수단 없음.
- **권장 조치**: `SHOW CREATE TABLE`로 실제 스키마와 모델 대조 후 필요한 제약 수동 보정, Alembic 도입 검토.

### 3-3. [중간] 카카오 알림 실패 시 디바운스 무효화 → 매 요청 최대 ~30초 반복
- **위치**: `app/services/admin_notify.py:180-183, 225-226`
- **심각도**: 중간
- **재현 시나리오**: 카카오 OAuth 미설정/토큰 갱신 실패 상태에서 품절 모델을 반복 문의받으면 발송 실패 시 `_last_notified`가 갱신되지 않아 매 요청마다 전체 알림 체인(경쟁사가격 조회+토큰갱신+발송, 각 최대 10초)이 재실행되어 지연 누적 및 DB 커넥션 점유.
- **권장 조치**: 발송 성공 여부와 무관하게(또는 더 짧은 별도 쿨다운으로) 디바운스 갱신, 또는 토큰 없음 상태면 조기 스킵.

### 3-4. [중간] 커넥션 풀(최대 7) 점유 패턴 + `pool_pre_ping` 미설정
- **위치**: `app/db/database.py:7-13`, `app/services/inventory.py:264-272, 282-283, 301`
- **심각도**: 중간
- **재현 시나리오**: DB 세션이 열린 채로 웹검색/CLOVA/카카오 호출이 이어져 세션을 수십 초간 점유. `pool_size=5+max_overflow=2=7`이 모두 점유되면 8번째 동시 요청이 `pool_timeout`(30초) 대기 후 타임아웃. `pool_pre_ping` 부재로 Tailscale 터널 재협상 시 죽은 커넥션이 그대로 재사용될 위험.
- **권장 조치**: `pool_pre_ping=True` 추가, 외부 API 호출 전 DB 세션을 먼저 닫는 구조 검토.

### 3-5. [낮음] FK cascade/ondelete 미정의 (현재 비활성 위험)
- **위치**: `app/db/models.py:61-62, 79, 104, 118, 131`
- **심각도**: 낮음
- **재현 시나리오**: 현재 `Product` 삭제 코드 경로가 없어 비활성. 향후 삭제 기능 추가 시 자식 행 정합성 문제 발생 가능.
- **권장 조치**: 삭제 기능 추가 계획이 있다면 `ondelete` 명시.

### 3-6. [낮음] `extra_specs` None 미가드 헬퍼 (현재 호출부는 안전)
- **위치**: `app/services/servo_spec_search.py:87-93, 96-100`
- **심각도**: 낮음
- **재현 시나리오**: 현재 호출 3곳 모두 사전에 not-None 보장되어 안전하나, 다른 파일 호출부와 스타일이 달라 향후 미필터링 호출 추가 시 크래시 위험.
- **권장 조치**: `s.extra_specs or {}` 가드로 통일.

### 3-7. [낮음] Tailscale 설정값이 config.py/.env.example과 분리되어 드리프트 위험
- **위치**: `tailscale_proxy.py:17-24`, `app/config.py:16`, `.env.example:7`
- **심각도**: 낮음
- **권장 조치**: 프록시 포트와 `DATABASE_URL`을 하나의 소스로 통합하거나 `.env.example`에 전체 관계 주석화.

---

## 4. 서보모터/감속기 로직 — "이중 안내문구" 버그 & feature flag

### 4-1. [H8] 이중 안내문구 중복 노출 — 근본 원인 확인
- **위치**: `app/services/servo_spec_search.py:393-399` (`_format_motor_spec_block` 내 `_REDUCER_ADAPTER_DISCLAIMER` 삽입) + `:434-441` (`find_reducer_compat` case 1)
- **심각도**: 높음
- **재현 시나리오**: `register_hc_kfs_servo.py:82-94`에서 코드 "10"에 `MR-J2S-10A/10B/10A1/10B1` 드라이브에 `HC-KFS053`, `HC-KFS13` 두 모터가 병합 등록되어 있고, 둘 다 축경 8mm로 AB042 감속기와 자동매칭됨. 사용자가 **드라이브 모델명**(예: "MR-J2S-10A")으로 조회하면 `find_reducer_compat` case 1이 `motor_specs.items()` 전체를 순회하며 모터 블록마다 🔩 어댑터 확인 문구를 반복 삽입하고, 함수 마지막에서 ⚠️ 치수 disclaimer를 한 번 더 붙여 서로 다른 두 안내문구가 겹쳐 보임.
- **6d71d65과의 관계**: 그 커밋은 `_dedupe_drive_pairs_by_family`로 **여러 드라이브(A/A1, B/B1) 간** 중복만 해소했고, **하나의 드라이브 안 다중 모터**(case 1) 경로는 손대지 않아 이번 버그가 남음.
- **테스트가 못 잡은 이유**: 기존 `test_hc_kfs_chatbot_flow.py`는 전부 **모터명**으로만 질의해 case 2(항상 블록 1개)만 검증하고, 드라이브명 질의 경로(case 1)는 커버하지 않음. 또 단언이 문구 포함 여부만 검사하고 출현 횟수를 세지 않음.
- **권장 조치**: `_REDUCER_ADAPTER_DISCLAIMER`를 모터 블록마다 반복 삽입하지 말고, 응답 조립 시 자동매칭 결과가 1건이라도 있으면 최종적으로 단 한 번만 붙이도록 구조 변경(치수 disclaimer와 동일 패턴 적용). 드라이브명 질의 테스트 케이스와 `reply.count(...)` 기반 출현 횟수 단언 추가.

### 4-2. [중간] `Reducer` 테이블에 유니크 제약 없음 + 등록 스크립트가 중복 삽입 방지 안 함
- **위치**: `app/db/models.py:158-181`, `register_apex_reducer.py:238-246`
- **심각도**: 중간
- **재현 시나리오**: 스크립트를 실수로 두 번 실행하면 동일 모델이 중복 저장되고, 매칭/포맷 로직이 모델명 기준 중복 제거를 하지 않아 감속기 매칭 결과가 여러 줄 반복 출력됨(4-1과 유사한 사용자 혼란).
- **권장 조치**: 등록 스크립트에 실행 전 존재 확인 절차 추가 또는 DB 레벨 유니크 제약.

### 4-3. [중간] APEX 감속기 자동매칭에 feature flag 없음
- **위치**: `app/config.py`, 저장소 전체 grep 결과 없음
- **심각도**: 중간
- **재현 시나리오**: 카탈로그 데이터 오류 발견 시에도 배포 없이 즉시 끌 방법이 없음. (반대로 "꺼져있는데 다른 코드가 참조" 하는 불일치 위험은 없음 — Reducer 관련 코드는 servo_spec_search.py/models.py 두 곳에만 존재.)
- **권장 조치**: `REDUCER_AUTO_MATCH_ENABLED` 같은 플래그 도입 검토.

### 4-4. [중간] 미병합 워크트리의 `register_motor_dimensions.py` — 실행 시 버그 악화
- **위치**: `.claude/worktrees/feature+servo-response-restructure/register_motor_dimensions.py` (미커밋, orphan)
- **심각도**: 중간
- **재현 시나리오**: 이 스크립트는 `--dry-run` 플래그를 갖춘 개선된 형태이나, 코드 "60"에 3개 모터(`HC-SFS52`+`HC-SFS53`+`HC-LFS52`)를 `MR-J2S-60A/60B`에 병합하는 등, 실행 시 4-1의 버그를 3중 이상으로 악화시킬 데이터를 추가함. 어디에도 커밋되지 않아 다른 개발자가 존재조차 모를 수 있음.
- **권장 조치**: 4-1 근본 수정 없이 이 스크립트를 실행/병합하지 말 것.

### 4-5. [낮음] revert 커밋 이력이 실제 상태와 불일치해 오해 유발
- **위치**: `f9b74fc` vs 현재 `test_hc_kfs_chatbot_flow.py`
- **심각도**: 낮음
- **비고**: 커밋 메시지는 테스트를 되돌렸다고 되어 있으나 머지 순서상 확장판이 현재 HEAD에 그대로 살아있음 — 실제로는 기능이 죽은 코드가 아님. 조치 불필요, 정보성 기록.

---

## 5. 3단계 폴백 체인 (DB → 네이버 검색 → CLOVA) & 인텐트 분류

### 5-1. [H6] "찾지 못했습니다" 문자열 매칭이 LLM 자유생성 텍스트에도 적용됨
- **위치**: `app/api/chatbot.py:109, 141` (대상: `app/services/replacement.py:105`, `app/services/specs.py:29`)
- **심각도**: 높음
- **재현 시나리오**: `find_replacement()`가 DB에서 정식 대체품을 찾아 CLOVA에게 문장 생성을 맡기는데, 프롬프트가 특정 문구 사용을 금지하지 않아 "정확한 커넥터 규격은 매뉴얼에서 찾지 못했습니다" 같은 헤지 표현이 답변 중간에 포함될 수 있음. 이 경우 `db_reply`에 `"찾지 못했습니다"`가 포함되어 정상적으로 찾은 DB 답변임에도 웹검색으로 덮어써짐 — 코드 주석이 명시적으로 경고하는 과거 "웹검색 AI가 근거없는 대체품을 지어낸" 사고와 동일 패턴이 LLM 자유생성 경로에 남아있음. `specs.py`도 동일 구조.
- **권장 조치**: 폴백 여부 판단을 LLM 출력 문자열 매칭 대신, 서비스 함수가 "DB에서 실제로 매칭됐는지"를 명시적 반환값(예: `(reply, matched: bool)`)으로 알려주도록 변경.

### 5-2. [중간] SPEC_SEARCH/SERVO_RECOMMEND 인텐트는 웹 폴백이 아예 없음
- **위치**: `app/api/chatbot.py:148-157` (대상: `app/services/spec_search.py:36-40`, `app/services/servo_spec_search.py:72-77`)
- **심각도**: 중간
- **재현 시나리오**: 이 두 인텐트는 DB 미스 시 "찾지 못했습니다" 텍스트를 검사 없이 그대로 반환 — 다른 인텐트(대체품/스펙/알람)와 달리 웹검색/CLOVA 지식 폴백이 시도되지 않아 인텐트 간 응답 품질이 비일관적. 의도적 설계인지 누락인지 근거 없음.
- **권장 조치**: 웹 폴백 정책을 인텐트 전체에 통일 적용할지 정책 결정 필요.

### 5-3. [중간] 서보드라이브 상세조회 시 두 독립 쿼리가 서로 다른 제품을 매칭할 가능성
- **위치**: `app/services/servo_spec_search.py:180-194` vs `app/services/replacement.py:12-23`
- **심각도**: 중간
- **재현 시나리오**: `find_servo_drive_details`가 별도로 `find_replacement`를 재호출하는데, 두 쿼리 모두 `ilike` + `ORDER BY` 없는 `.first()`라 부분일치 모델명 질의 시 "단종/대체품" 섹션과 "타사비교" 섹션이 서로 다른 제품을 설명하는 내부 모순 응답이 만들어질 수 있음.
- **권장 조치**: `find_servo_drive_details`가 확정한 product를 `find_replacement`에 전달해 재사용, 또는 두 쿼리에 결정적 정렬 기준 추가.

### 5-4. [H7] 제조사명 표기 불일치 — 매뉴얼 업로드 경로
- **위치**: `app/api/manual.py:30`, `app/services/pdf_processor.py:197, 203, 210`, `app/db/seed.py:153-224`
- **심각도**: 높음
- **재현 시나리오**: seed 데이터는 전부 영문 `"Mitsubishi"`인데 관리자 업로드 폼 설명은 `제조사명 (예: 미쓰비시)`. 관리자가 폼 예시대로 "미쓰비시"를 입력하면 (1) 기존 "Mitsubishi" 알람코드와 정확 일치 dedup 체크(`==`)가 걸리지 않아 중복 저장, (2) `GET /api/manual/alarms/{manufacturer}` 등 조회 필터도 정확 일치라 절반이 누락, (3) 제조사별 통계도 왜곡. CLAUDE.md에 명시된 대로 로컬 처리 결과가 그대로 배포되므로 한 번의 실수가 영구 반영됨.
- **권장 조치**: 폼 예시를 seed와 일치시키거나(영문 표준화), 저장/조회 전 정규화 매핑 테이블 도입.

### 5-5. [중간] `_web_fallback()` 2차 CLOVA 호출 미보호 + 총 지연시간 상한 없음
- **위치**: `app/api/chatbot.py:78-94`, `app/services/web_search.py:66-72, 80-89, 97-106`
- **심각도**: 중간
- **재현 시나리오**: CLOVA가 완전 다운이 아니라 느리게 응답하는 상황에서 네이버검색(~10초)+웹검색용 CLOVA(~30초, 실패)+지식기반 백업 CLOVA(~30초)가 순차 실행되어 최악 약 70초까지 걸릴 수 있음. 전역 타임아웃/서킷브레이커가 없어 Render 리버스 프록시 타임아웃으로 502/504 위험.
- **권장 조치**: 2차 CLOVA 호출도 try/except로 감싸고, `_web_fallback()` 전체에 `asyncio.wait_for`로 상한 시간 설정.

---

## 6. 카카오 OAuth 알림 파이프라인

### 6-1. [H9] 카카오 API 예외가 고객 응답까지 깨뜨림
- **위치**: `app/services/admin_notify.py:72-89` (`_get_valid_kakao_access_token`), `:102-107` (`_send_kakao_text`), 호출부 `app/services/inventory.py:283, 301`, 최종 캐치 `app/api/chatbot.py:58-66`
- **심각도**: 높음
- **재현 시나리오**: 고객이 품절 모델 조회 → `notify_admin_kakao()` 호출 시점에 카카오 서버 타임아웃/DNS 실패 발생. 토큰 갱신/발송 함수 모두 try/except 없이 httpx 호출을 실행하고 호출부도 감싸져 있지 않아 예외가 `chatbot.py:58-66`까지 전파됨 — 여기 걸리면 원래 전달됐어야 할 재고/대체품 안내 전체가 사라지고 범용 오류 메시지로 대체됨. **관리자 알림 실패가 고객 응답 실패로 번지는 설계적 결함**.
- **권장 조치**: 카카오 관련 함수들을 자체 try/except로 감싸 항상 `bool`을 반환하도록 방어. 고객 응답 경로와 관리자 알림 경로를 완전히 분리(백그라운드 태스크화) 검토.

### 6-2. [H10] `refresh_token` 만료 시 영구 침묵 실패, 감지 수단 없음
- **위치**: `app/services/admin_notify.py:82-84`
- **심각도**: 높음
- **재현 시나리오**: 카카오 `refresh_token`은 일정 기간 후 만료됨. 만료/revoke 시 로그 한 줄만 남기고 `None` 반환 — 이후 모든 저재고 이벤트에서 알림이 계속 조용히 실패함. 저장소 전체에 로그 외 오류 추적 수단(이메일/Slack 등)이 없어 개발자가 로그를 직접 보거나 수동 재인증하기 전까지 관리자는 알림 중단 사실을 전혀 모름.
- **권장 조치**: 토큰 갱신 실패 시 별도 채널로 "재인증 필요" 1회성 알림, 또는 DB에 실패 상태 기록해 헬스체크에서 노출.

### 6-3. [중간] 디바운스 키가 재고 상태 악화를 구분하지 못함
- **위치**: `app/services/admin_notify.py:32-33, 180-183`
- **심각도**: 중간
- **재현 시나리오**: 디바운스가 `model_name`만으로 걸려, 저재고→품절로 상태가 급격히 악화돼도 1시간 윈도우 내라면 알림이 나가지 않아 관리자가 최대 50분 가까이 실제 품절 사실을 모를 수 있음.
- **권장 조치**: 디바운스 키를 `(model_name, stock_state)`로 변경해 상태 악화는 디바운스 우회.

### 6-4. [중간] 토큰 갱신 경쟁 조건 — 락 없음
- **위치**: `app/services/admin_notify.py:62-89`, `app/db/models.py:186-193` (`KakaoToken`)
- **심각도**: 중간
- **재현 시나리오**: 두 요청이 거의 동시에 만료 판정을 내려 동일 refresh_token으로 동시 갱신 시도. 잠금/버전 컬럼이 없어, 카카오가 refresh_token 재사용 시 회전/무효화하는 정책이면 한쪽이 `invalid_grant`로 실패하거나 무효화된 토큰으로 발송을 시도할 수 있음.
- **권장 조치**: `asyncio.Lock()` 또는 DB `SELECT ... FOR UPDATE`로 갱신 직렬화.

### 6-5. [낮음] 디바운스가 인메모리라 재배포 시 초기화
- **위치**: `app/services/admin_notify.py:33`
- **심각도**: 낮음
- **재현 시나리오**: 재배포 직후 디바운스가 리셋돼 동일 모델에 중복 알림 발송 가능(유실은 아니고 성가심 수준).
- **권장 조치**: 이미 존재하는 `StockAlert.sent_at`을 디바운스 기준으로 재활용.

### 6-6. [낮음] Slack 알림 경로가 out_of_stock에만 비대칭 호출
- **위치**: `app/services/inventory.py:282-283, 301, 321-336`
- **심각도**: 낮음
- **재현 시나리오**: Slack은 `out_of_stock`에서만 카카오와 함께 호출되고 `low_stock`에서는 호출 안 됨 — 관리자가 채널 유무로 상태를 오판할 여지. 단, 이 경로는 자체 예외처리가 되어 있어 고객 응답을 깨지는 않음.
- **권장 조치**: Slack 사용 여부 확인 후 정리 또는 커버리지 통일.

### 6-7. [낮음] 네이버쇼핑 응답 파싱이 `.get()` 없이 직접 인덱싱
- **위치**: `app/services/admin_notify.py:116-139`
- **심각도**: 낮음
- **비고**: 상위 try/except가 잡아 전체 알림은 나가지만 경쟁사 가격 전체가 조용히 빠짐.
- **권장 조치**: `.get()` 사용, 개별 아이템 파싱 실패는 해당 아이템만 skip.

### 6-8. [낮음] `kakao_test/` 하드코딩 자격증명 + gitignore 누락
- **위치**: `kakao_test/kakao_auth_setup.py:17, 21`
- **심각도**: 낮음
- **비고**: 현재 미추적 상태지만 `.gitignore`에 없어 향후 실수로 커밋될 위험.

---

## 7. 데이터 마이그레이션/매칭 스크립트 (repo 루트)

> **사실관계**: 2026-07-02 커밋 `35366a9`를 기점으로 `origin_product_no` 사전매핑 방식은 라이브 재고조회 경로에서 완전히 제거되고 모델명 기반 실시간 검색(`search_stock_by_model_name`)만 사용됨. `Product.origin_product_no`/`smartstore_product_id`/`inventory_sync_enabled` 컬럼은 여전히 DB에 존재하지만 라이브 코드에서 읽는 곳은 없음(죽은 컬럼). 단, `naver_commerce.py`의 `get_live_stock_quantity(origin_product_no)`는 "관리자 지정 조회용"으로 여전히 살아있고 호출 가능.

### 7-1. [H11] `migrate_origin_product_no.py` — 폐기된 접근법이 경고 없이 재실행 가능
- **위치**: 전체, 특히 `:55-123`
- **심각도**: 높음
- **재현 시나리오**: 신규 개발자가 원상품번호 매핑이 비어있는 걸 보고 이 스크립트를 프로덕션 DB(Tailscale로 연결된 NAS MariaDB)에 재실행하면 `origin_product_no`가 채워짐. 현재는 죽은 컬럼이지만, 여전히 살아있는 `get_live_stock_quantity()`가 향후 다시 연결되면 즉시 "재고 있는데 없다" 버그가 재현됨. Git 이력에도 잡히지 않는 untracked 파일이라 이력 추적도 안 됨.
- **권장 조치**: 파일 최상단에 명시적 DEPRECATED 경고 추가 또는 완전 삭제/`deprecated/` 이동.

### 7-2. [H12] `finalize_matching.py` — 실증된 오매칭 버그를 그대로 encode
- **위치**: `:38-42` (`KNOWN_BAD_MATCHES`), `:90-149`
- **심각도**: 높음
- **재현 시나리오**: `naver_commerce.py` 주석이 인용하는 MR-J2S-40B 오매칭 건이 정확히 이 파일의 `KNOWN_BAD_MATCHES`와 일치 — 즉 이 스크립트의 매칭 로직 자체가 나중에 잘못된 것으로 판정난 로직. 2주 이상 경과한 스냅샷 JSON을 근거로 재실행하면 프로덕션에 부정확한 상태가 조용히 쌓이고, 향후 관련 컬럼이 다시 참조되는 순간 문제가 폭발.
- **권장 조치**: 명시적 폐기 표시 또는 삭제. 관련 스냅샷 JSON 3종도 정리 대상.

### 7-3. [중간] `apply_manual_mappings.py` — 안전장치를 의도적으로 우회
- **위치**: `:1-4` (docstring), `:62-79`
- **심각도**: 중간
- **재현 시나리오**: 자체 docstring에 `finalize_matching.py`의 `KNOWN_BAD_MATCHES` 제외 로직을 우회한다고 명시. `manual_mappings.json`은 현재 플레이스홀더 상태(`is_placeholder()` 체크로 실행해도 no-op)라 즉시 위험하지는 않으나, 실제 값이 채워지는 순간 이미 오매칭으로 판명난 안전장치 우회 로직이 프로덕션에 반영됨.
- **권장 조치**: 스크립트와 JSON 모두 삭제 또는 명시적 폐기 표시.

### 7-4. [낮음] `resolve_dups.py` — 원시 SQL을 사람이 복사해 수동 실행하도록 유도
- **위치**: `:41-47`
- **심각도**: 낮음
- **비고**: 스크립트 자체는 read-only이나 출력된 SQL을 그대로 실행하면 백업/트랜잭션 없이 폐기된 컬럼에 즉시 반영됨.

### 7-5. [낮음] `backup_specifications.py` — 문서화된 복구 스크립트 부재
- **위치**: `:8` (docstring)
- **심각도**: 낮음
- **비고**: "복구가 필요하면 `backups/specifications_restore.py` 참고"라고 명시하나 해당 파일은 저장소에 존재하지 않음.
- **권장 조치**: 실제로 작성하거나 문구 제거.

### 7-6. [낮음] 미커밋 사업 데이터 JSON들의 gitignore 누락
- **위치**: `.gitignore`, `products_raw.json` 등
- **심각도**: 낮음
- **비고**: 재고수량 등 사업 데이터가 포함된 스냅샷 JSON이 `.gitignore`에 없어 실수로 커밋될 위험.

### 7-7. 확인된 안전 항목 (문제 없음)
- 검토 대상 스크립트 전체에서 하드코딩된 자격증명 없음(모두 `.env`/`app.config` 경유).
- `app/` 어디에서도 이 루트 스크립트들을 `import`하지 않음 — 라이브 요청 경로 의존성 없음.
- `manual_mappings.json`은 `apply_manual_mappings.py` 외 어디서도 참조되지 않고 현재 플레이스홀더 상태.
- `analyze_product_matching.py`, `diagnose_unmatched.py`, `fetch_smartstore_products.py`, `debug_search.py`, `probe_enum.py`, `probe_keyword_field.py`는 모두 읽기 전용/DB 미접속으로 확인됨.

---

## 8. 기타 저장소 위생 (참고, 조치 불필요/저우선순위)

- 저장소 루트에 내용이 빈 `dir`라는 이름의 파일이 커밋되어 있다가 현재 삭제 대기 상태(`git status`상 `deleted: dir`) — 과거 실수로 커밋된 것으로 보이며 실질적 위험 없음.
- `.claude/worktrees/feature+servo-response-restructure`는 `master`와 커밋 차이가 없어 "병합 안 된 브랜치"로 인한 충돌 위험은 없음. 다만 4-4에서 다룬 대로 워크트리 내 미추적 스크립트가 존재.

---

## 부록: 이번 리뷰에서 다루지 않은 것
- 실제 프로덕션 MariaDB 스키마 직접 조회(`SHOW CREATE TABLE`)는 접근 권한 범위 밖이라 수행하지 못함 — H5는 마이그레이션 스크립트 코드 분석에 기반한 강한 추정.
- 프로덕션 DB의 `Reducer` 테이블 실제 중복 여부는 확인하지 못함(4-2는 구조적 위험으로만 보고).
