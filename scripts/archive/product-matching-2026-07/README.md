# 상품 매칭 파이프라인 (2026-07, 완료됨)

스마트스토어 전체 상품 카탈로그를 모델명 기준으로 DB와 매칭시키기 위해 2026년 7월에 한 번 실행한 일회성 파이프라인입니다. 결과는 이미 DB에 반영 완료되었습니다. **재실행하지 마세요** — 카탈로그가 이미 바뀌었을 수 있고, 재실행 시 `finalize_matching.py`/`apply_manual_mappings.py`가 현재 DB 상태를 덮어쓸 수 있습니다.

## 실행 순서 (참고용)

1. `fetch_smartstore_products.py` — 스마트스토어 API로 전체 상품 목록 수집 → `products_raw.json`
2. `analyze_product_matching.py` — 모델명 기준 매칭 분석 → `matching_analysis.json`
3. `diagnose_unmatched.py` — 미매칭 원인 분류 → `unmatched_diagnosis.json`
4. `resolve_dups.py` — 중복 후보 추출
5. `backup_specifications.py` — DB 반영 전 안전장치(백업)
6. `finalize_matching.py` — 최종 매칭 결과 DB 반영
7. `apply_manual_mappings.py` — 수동 검증 매핑 강제 반영

`migrate_origin_product_no.py`는 같은 시기의 별도 마이그레이션(채널상품번호 → 원상품번호)으로, 이후 `naver_commerce.py`의 모델명 기반 실시간 검색으로 대체되었습니다(자세한 배경은 `CLAUDE.md`의 "루트 레벨 일회성 스크립트" 섹션 참고).
