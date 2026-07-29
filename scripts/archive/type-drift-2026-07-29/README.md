# 타입 드리프트 보정 (완료, 2026-07-29)

H5(`../h5-schema-drift-2026-07-28/`)에서 "위험 대비 효과가 낮다"며 보류했던 타입 드리프트 중,
실제 기능 결함이 확인된 부분을 프로덕션 MariaDB에 적용 완료.

## 왜 했나

재고 알림 기능(고객=재고 여부만 / 관리자=타사 단가 포함) 실동작 테스트 중 발견:

1. `stock_alerts.sent_at`, `price_history.checked_at`이 `text` 컬럼이라
   ORM의 `server_default=func.now()`가 적용되지 않았다 — 서버 기본값은 `CREATE TABLE`
   시점에만 반영되는데, 테이블이 `pandas.to_sql`로 만들어졌기 때문. 알림은 정상 발송되지만
   **언제 나갔는지 기록이 전부 NULL**이라 감사 로그로 쓸 수 없었다. 같은 이유로
   `products.created_at`도 133건 중 97건이 NULL.
2. `products.our_price`가 `double`이라 관리자 카카오 알림에 `판매단가: 420,000.0원`으로
   소수점이 붙어 출력됐다 (ORM 선언은 `Integer`).

## 적용 내용

- `text` → `DATETIME DEFAULT CURRENT_TIMESTAMP`: `products.created_at`/`updated_at`,
  `replacements.created_at`, `inventory.last_updated`, `stock_alerts.sent_at`,
  `price_history.checked_at`
- `text` → `DATETIME NULL`: `stock_alerts.resolved_at` (ORM에도 기본값 없음 — 해소 시점에만 기록)
- `products.our_price`: `double` → `BIGINT`
- `price_history`의 `our_price`/`competitor_min`/`avg`/`max`/`count`: `text` → `BIGINT`,
  `diff_percent`: `text` → `DOUBLE`
- `text` → `TINYINT(1) DEFAULT 0`: `stock_alerts.resolved`, `price_history.needs_adjustment`

기존 NULL 시각은 실제 값을 알 수 없어 backfill하지 않고 NULL로 두었다. 이후 INSERT부터
기록된다.

## 검증

실행 전(프로덕션 직접 조회): DateTime 후보 7개 컬럼 `CAST(... AS DATETIME)` 실패 0건,
숫자 후보 컬럼 비숫자/소수부 0건, boolean 후보는 `'0'`/`'1'`만 존재.
전체 백업: `backups/full_backup_pre_typefix_20260729T014452Z.json` (9개 테이블, gitignore 대상).

실행 후:
- 백업 JSON과 현재 값 대조 — 변환 대상 5개 테이블 160행 전체 불일치 0건 (데이터 손실 없음)
- 재고 알림 재실행 → `stock_alerts.sent_at` / `price_history.checked_at`에 시각 기록 확인,
  카카오 메시지 `판매단가: 420,000원`으로 정상 출력
- INSERT/UPDATE 후 롤백 방식으로 `products.created_at`·`updated_at`,
  `replacements.created_at`, `inventory.last_updated` 자동 기록 확인
- `test_inventory.py` 정상 동작 (읽기 경로 회귀 없음)

**재실행하지 마세요** — 이미 프로덕션에 적용 완료된 일회성 스크립트입니다.

## 여전히 적용하지 않은 것

- `products.status`, `stock_alerts.channel` 등 ENUM 후보 컬럼은 `text` 유지
  (현재 값이 정상이고 ORM이 문제없이 읽고 있음)
- `series`/`manufacturer`/`category` 등 `text` → `VARCHAR(50)` 축소 (실익 적음)
- `replacements.program_convertible` 등 `BIGINT`(사실상 0/1) → `BOOLEAN` 정규화
