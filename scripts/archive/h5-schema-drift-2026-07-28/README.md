# H5 스키마 드리프트 보정 (완료, 2026-07-28)

코드리뷰(2026-07-17) H5 대응. 프로덕션 MariaDB에 다음을 적용 완료:

- `products.model_name`: TEXT → `VARCHAR(100) NOT NULL` + UNIQUE 인덱스
- `stock_alerts.product_id`, `price_history.product_id`: TEXT → `BIGINT NOT NULL`
- `inventory.product_id`, `specifications.product_id`: UNIQUE 인덱스 추가 (1:1 관계 보장)
- `inventory`/`specifications`/`replacements`(old·new)/`stock_alerts`/`price_history`의
  `*_id` 컬럼에 `products.id` 참조 FK 추가

**재실행하지 마세요** — 이미 프로덕션에 적용 완료된 일회성 스크립트입니다. 실행 전 백업은
로컬 `backups/full_backup_pre_h5_<timestamp>.json`에 저장돼 있습니다(gitignore 대상,
로컬에만 존재).

적용하지 않은 것(별도 판단 필요, 위험도/효과 대비 낮은 우선순위로 보류):

> 2026-07-29 갱신: 아래 중 DATETIME 전환과 숫자/BOOLEAN 타입 보정은
> `../type-drift-2026-07-29/`에서 적용 완료(알림 시각이 전부 NULL로 남는 실제 결함 확인).

- `products.status` 등 ENUM 후보 컬럼을 TEXT로 유지 (현재 값은 `ACTIVE`/`DISCONTINUED`만
  존재해 안전하게 전환 가능하나, 이번 범위에서는 제외)
- `created_at`/`updated_at`/`sent_at` 등 TEXT로 저장된 날짜 컬럼을 `DATETIME`으로 전환
- `series`/`manufacturer`/`category` 등 TEXT → `VARCHAR(50)` 타입 축소(인덱스는 이미 존재하지
  않고 `ilike` 부분일치 검색 위주라 실익이 적음)
- `replacements.program_convertible` 등 `BIGINT`(사실상 0/1) → `BOOLEAN` 정규화(기능상
  문제 없어 보류)
