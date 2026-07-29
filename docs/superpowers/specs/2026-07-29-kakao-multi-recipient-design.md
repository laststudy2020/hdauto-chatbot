# 재고 알림 다중 수신자 + 응대 규칙 정비 설계

작성일: 2026-07-29

## 배경

재고 알림 기능(고객에게는 재고 여부만, 관리자에게는 경쟁사 단가 포함)을 실제 프로덕션에서
검증한 결과 정상 동작을 확인했다. 이후 사장님이 정리한 "재고 조회 응대 규칙" 문서와 현재
구현을 대조해 네 가지 차이를 확인했고, 이번 작업에서 그 차이를 메운다.

| 규칙 문서 | 현재 구현 |
|---|---|
| 재고 조회 성공 시 매번 관리자 알림 | `low_stock`/`out_of_stock`일 때만 알림 |
| DB 미매칭 → "확인 후 안내드리겠습니다" | 미매칭도 "재고 없음"으로 단정 (`inventory.py`) |
| 경쟁사 단가에서 "해외" 표기 상품 제외 | 필터 없음 |
| 알림에 조회 시각 포함 | 없음 |
| 관리자 수신자 다수 | `kakao_tokens` 싱글턴 1인 |

## 확정된 결정

브레인스토밍에서 사장님이 선택한 사항이다. 구현 중 흔들리지 않도록 근거와 함께 남긴다.

1. **수신자 구조: A+** — 문서의 `alarm_recipients` 테이블을 그대로 만들되 채널은 `kakao`만
   구현한다. 관리자별 OAuth 토큰을 행으로 저장하고 활성 수신자 전원에게 순차 발송한다.
   나중에 slack/email을 붙일 때 스키마를 다시 바꾸지 않아도 된다.
2. **알림 트리거: 전부** — 재고가 충분한 문의도 알림한다. "어떤 상품을 고객이 찾고 있는가"를
   수요 신호로 쓰기 위함. 모델별 1시간 디바운스는 유지해 발송량을 억제한다.
3. **경쟁사 필터 범위: 해외만** — 문서에 적힌 범위대로 한다. 중고/부속품으로 인한 최저가
   오탐(MR-J4-70A: 자사 42만원 vs 중고 1.27만원, `needs_adjustment=True` 오탐)이 남는 것은
   인지하고 감수한다. 키워드를 DB 테이블에 두므로 운영 중 배포 없이 확장할 수 있다.
4. **미매칭 응답: "확인 후 안내"** — 카탈로그에 없는 모델을 "재고 없음"으로 단정해 주문을
   놓치는 일을 막는다.
5. **수신자 등록: 링크 클릭 자가등록** — 초대 링크를 카톡으로 보내면 받는 사람이 눌러서
   동의하고 자동 등록된다. 사람이 늘어도 개발자 개입이 필요 없다.

## 구현 순서

카카오 개발자콘솔 설정(사장님 작업)과 두 번째 카카오 계정이 필요한 부분을 뒤로 미뤄,
외부 의존 없이 완료 가능한 것을 먼저 끝낸다.

- **1단계 (외부 의존 없음)**: 테이블 2개, 해외 필터, 고객 3분기 응답, 알림 트리거 확대,
  다중 수신자 루프 + 실패 격리. 수신자는 사장님 1명으로 두고 실동작 검증까지 완료한다.
- **2단계 (콘솔 설정 후)**: 자가등록 엔드포인트. 링크 테스트는 설정 완료 후 수행한다.

## 1. 데이터 모델

### `alarm_recipients` (신규)

```python
class AlarmRecipient(Base):
    __tablename__ = "alarm_recipients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False)
    channel = Column(String(20), nullable=False, default="kakao")
    channel_token = Column(Text, nullable=False)   # 카카오: refresh_token
    access_token = Column(Text)                    # 카카오 채널 캐시 (nullable)
    token_expires_in = Column(Integer)             # 카카오 채널 캐시
    token_obtained_at = Column(DateTime)           # 카카오 채널 캐시
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
```

`channel_token`은 채널마다 의미가 다른 영속 자격증명이다(카카오=refresh_token, 훗날
slack=webhook URL). `access_token`/`token_expires_in`/`token_obtained_at`은 카카오 채널만
사용하는 단기 캐시이며 다른 채널에서는 NULL로 둔다. 채널별 JSON 컬럼 대신 명시 컬럼을 쓰는
이유는 조회·디버깅이 쉽고 지금 필요한 채널이 하나뿐이기 때문이다.

### `price_filter_keywords` (신규)

```python
class PriceFilterKeyword(Base):
    __tablename__ = "price_filter_keywords"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String(50), nullable=False, unique=True)
    is_active = Column(Boolean, default=True, nullable=False)
    note = Column(String(200))
```

초기 시드: `해외`, `구매대행`, `해외배송`, `직구`.

### 기존 `kakao_tokens` 이관

`kakao_tokens`의 단일 행(사장님 토큰)을 `alarm_recipients`의 첫 행으로 옮기고, 코드는 더 이상
`kakao_tokens`를 읽지 않는다. 토큰 출처가 두 곳이면 갱신된 refresh_token이 한쪽에만 반영돼
다른 쪽이 조용히 죽는다. 테이블 자체는 롤백 대비로 남기되 `KakaoToken` 모델은 코드에서
참조하지 않는다.

프로덕션 테이블은 `pandas.to_sql`로 만들어진 이력이 있어 ORM 선언이 DDL에 반영되지 않는
문제가 있었다(2026-07-28 H5, 2026-07-29 타입 드리프트 보정 참고).

> **2026-07-29 정정:** 처음에는 신규 두 테이블을 마이그레이션 스크립트의 명시적
> `CREATE TABLE`로 만들 계획이었으나, 불필요한 것으로 확인됐다. `init_db()`가 시작 시
> `Base.metadata.create_all`을 호출하고 SQLAlchemy가 생성하는 DDL은 ORM 선언 그대로다.
> `pandas.to_sql` 드리프트는 최초 이관 테이블에만 해당한다. 마이그레이션 스크립트는
> `create_all(checkfirst=True)` 호출과 데이터 시드만 담당한다. 적용 후
> `information_schema`로 확인한 결과 `name`/`channel`은 `varchar`, `is_active`는
> `tinyint(1)`, `created_at`은 `datetime` + `DEFAULT current_timestamp()`,
> `keyword`에 UNIQUE가 정상 생성됐다.

## 2. 발송 흐름 (`app/services/admin_notify.py`)

`notify_admin_kakao()`를 `notify_admins()`로 바꾸고 수신자 루프를 추가한다.

```
notify_admins(db, product, model_name, stock_qty, stock_state)
  1. 모델별 1시간 디바운스 판정 (수신자 전체 공통 1회)
  2. 경쟁사 단가 조회 → 해외 키워드 필터 적용
  3. 메시지 조립 (조회 시각, 제외 건수 포함)
  4. is_active=1 AND channel='kakao' 수신자별로:
       토큰 유효성 확인/갱신 → memo/default/send
  5. StockAlert 1행 기록 (알림 발생 단위 — 수신자 수만큼 늘리지 않는다)
  6. PriceHistory 기록 (기존과 동일: 경쟁사 결과와 our_price가 모두 있을 때만)
```

### 실패 격리

수신자 한 명의 실패가 다른 수신자 발송이나 고객 응답을 막아서는 안 된다. 관리자 알림은
부가 기능이고, 여기서 예외가 새면 고객이 받아야 할 재고 응답 전체가 범용 오류 메시지로
대체된다(2026-07-17 코드리뷰 H9에서 이미 겪은 문제).

- 수신자별 발송을 개별 `try/except`로 감싼다.
- 토큰 갱신 실패는 수신자 id별로 기록한다. 현재 `_kakao_last_failure`가 전역 단일 값이라
  누구의 토큰이 끊겼는지 알 수 없다. `{recipient_id: {...}}`로 바꾸고
  `get_kakao_notify_health()`가 수신자별 상태를 반환하도록 한다.
- `notify_admins()` 전체도 호출부에서 예외가 새지 않도록 방어한다.

### 디바운스

모델명 기준 1시간, 프로세스 내 dict(재시작 시 초기화)를 유지한다. 수신자별이 아니라 모델별로
한 번 판정한다 — 같은 알림이 수신자마다 다른 시점에 나가면 대조가 어렵다.

트리거가 "전부"로 확대되므로 디바운스가 발송량을 억제하는 유일한 장치가 된다. 인기 모델
문의가 몰리면 시간당 1건으로 수렴한다.

## 3. 알림 트리거 확대

`chatbot._route`가 아니라 `get_inventory_status()` 안에서 호출을 유지한다. 웹챗과 톡톡이 같은
함수를 타므로 한 곳만 고치면 양쪽에 반영된다.

- `in_stock`에도 `notify_admins()` 호출을 추가한다.
- `low_stock`/`out_of_stock`은 기존과 동일.
- 미매칭(아래 4번의 "확인 불가")은 호출하지 않는다 — 규칙 문서가 "매칭에 성공한 경우에만"으로
  명시했다.

## 4. 고객 응답 3분기 (`app/services/inventory.py`)

| 상태 | 응답 |
|---|---|
| 있음 | 현행 유지 — `✅ 재고 있음`, 수량 미노출 |
| 없음 | 현행 유지 — `📦 재고 없음` + 단종/대체품 안내 |
| 확인 불가 | 신설 — "정확한 재고 확인을 위해 확인 후 안내드리겠습니다" + 연락처 |

"확인 불가"는 **DB 카탈로그에도 없고 스마트스토어 실시간 검색에도 잡히지 않은** 경우다.
현재 코드는 이 경우를 `out_of_stock`으로 접어넣고 있다(`inventory.py`의 `_check_naver_directly`
실패 경로).

두 가지 세부 결정:

- **대체품 안내는 유지한다.** 미매칭이어도 웹검색 기반 유사 사양 안내는 고객에게 유용하다.
  피해야 할 것은 재고 유무를 단정하는 것뿐이다.
- **`low_stock` 문구는 현행 유지한다.** `✅ 재고 있음 (소진 임박 — 서두르시는 걸 권장드립니다)`는
  수량을 노출하지 않으면서 구매 전환에 도움이 된다.

수량·원가·마진은 어떤 분기에서도 고객 응답에 넣지 않는다. 현재 구현은 이미 이를 지키고 있고,
검증 스크립트로 회귀를 막는다.

## 5. 경쟁사 단가 해외 필터

`_get_competitor_prices()`에 필터를 추가한다.

- 판정 대상: 상품명(`title`)과 쇼핑몰명(`mallName`)
- 활성 키워드가 하나라도 포함되면 제외
- 자사몰 제외(`MY_MALL_KEYWORDS`)는 기존대로 유지하며, 이는 "제외 건수"에 포함하지 않는다
  — 관리자가 알아야 할 것은 해외 상품이 몇 건 빠졌는지이지 자사몰이 빠진 사실이 아니다

메시지 표기:

```
일부 제외 → 기존 목록 아래에 "※ 해외 표기 상품 N건은 비교 대상에서 제외됨"
전부 제외 → "경쟁사 단가: 해외 표기 상품으로 제외됨"
검색 결과 자체가 없음 → 기존대로 "타사 가격 검색 결과 없음"
```

키워드는 요청마다 DB에서 읽되, 프로세스 내 짧은 TTL 캐시(5분)를 둔다. 알림 1건마다 별도
쿼리를 날릴 이유가 없다.

## 6. 알림 메시지 포맷

```
📦 재고 알림
모델: MR-J4-70A
─────────────
상태: 재고 부족 (2개)
판매단가: 420,000원
─────────────
타사 가격 (참고용, 상품 일치 여부는 직접 확인 필요):
· [케이씨샵] 12,700원 - (중고)미쓰비시 서보 드라이브 MR-J4-70A
· [히트텐] 16,820원 - 미쓰비시 MR-J4-70A.HG-KR73 서보 모터
※ 해외 표기 상품 1건은 비교 대상에서 제외됨
─────────────
조회 시각: 2026-07-29 14:32
```

조회 시각은 알림 발송 시각으로 한다(조회와 발송 사이 지연은 초 단위라 구분 실익이 없다).

## 7. 수신자 자가등록 (2단계)

`app/api/admin.py`에 엔드포인트 2개를 추가한다.

- `GET /api/admin/recipients/connect?key=<ADMIN_COMMAND_KEY>&name=<이름>`
  → 논스를 발급해 서명된 `state`에 담고 카카오 동의 화면으로 302
- `GET /api/admin/recipients/callback?code=&state=`
  → `state` 검증(서명 + 논스 미사용 확인) → 토큰 교환 → `alarm_recipients` upsert
  → 완료 안내 HTML 반환

초대 링크에 `ADMIN_COMMAND_KEY`가 들어가므로 링크가 유출되면 임의의 사람이 수신자로 등록될
수 있다. 이를 막기 위해 **링크는 1회용**으로 만든다 — 논스를 발급 시 기록하고 콜백에서
소진시킨다. 논스 저장은 프로세스 내 dict로 하되 만료(30분)를 둔다. 재시작 시 발급된 링크가
무효화되지만, 초대 직후 사용하는 흐름이라 실용상 문제가 없다.

### 사장님 선행 작업

이 단계는 카카오 개발자콘솔 설정 없이는 동작 확인이 불가능하다.

1. 배포 도메인의 Redirect URI 등록 (현재 `http://localhost:5000/oauth`만 등록됨)
2. 앱이 "개발 중" 상태이면 추가 수신자를 팀원으로 등록하거나 배포 상태로 전환

## 8. 검증

프로젝트 관례대로 pytest 없이 `asyncio.run(main())` 형태의 standalone 스크립트로 검증한다.

- `test_multi_recipient_notify.py`
  - 활성 수신자 목록 조회 → 해외 필터 적용 → 메시지 조립까지 **발송 없이** 확인
  - 그 다음 실제 발송 1회
  - 고객 응답에 수량·타사 단가·쇼핑몰명이 없음을 문자열 단위로 검사(회귀 방지)
- 해외 키워드가 실제로 걸리는지 해외 매물이 잡히는 모델로 확인
- 수신자 1명의 토큰을 고의로 손상시켜, 나머지 수신자 발송과 고객 응답이 온전한지 확인
- 미매칭 모델(`존재하지않는모델XYZ999`)이 "확인 후 안내"로 응답하고 알림이 발송되지 않는지 확인
- 프로덕션 DB 변경 전 전체 백업 (`backups/`, gitignore 대상)

## 범위에서 제외한 것

- **중고/부속품 필터** — 결정 3에 따라 이번 범위 밖. 필요해지면 `price_filter_keywords`에
  행만 추가하면 된다.
- **관리자 웹 UI** — 수신자 추가는 초대 링크로, 비활성화는 DB 직접 수정으로 처리한다.
  사람이 몇 명 수준이라 화면을 만들 실익이 없다.
- **slack/email 채널 구현** — 스키마만 열어두고 구현하지 않는다.
- **`kakao_tokens` 테이블 삭제** — 이관 후에도 롤백 대비로 남긴다.
