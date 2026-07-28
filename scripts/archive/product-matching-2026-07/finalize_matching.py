"""
지금까지의 분석 결과를 종합해 최종 매칭을 확정하고 DB에 반영한다.

대상 분류:
  1. 1단계 단일매칭 43건           -> 자동 반영
  2. [C] 부분일치 중 안전 패턴만   -> 자동 반영
     (모델명 뒤에 영숫자가 바로 이어붙지 않고 공백/괄호/대시로 끊기는 경우만 안전 처리.
      예) "MR-J4-70B (...)" 안전 / "MR-J2S-700B-RG171" 위험(다른 품목) / "PFXGP4301TADW" 위험(오매칭))
  3. [B] 표기차이 2건              -> 모두 위험 패턴(부속/사양품)으로 판단, 제외
  4. [A] 미등록 69건               -> inventory_sync_enabled=False 로 표시만, 매핑 안 함
  5. 중복 4건                       -> 매핑하지 않고 목록만 출력 (수동 결정 대기)

사전 필수:
  - app/db/models.py의 Product 클래스에 아래 두 컬럼이 추가되어 있어야 함:
        origin_product_no = Column(String(50))
        inventory_sync_enabled = Column(Boolean, default=True)
    (마이그레이션 또는 ALTER TABLE 선행 필요. 컬럼이 없으면 이 스크립트 실행 시 에러 발생)

실행:
  python finalize_matching.py --dry-run   # 반영될 내용만 미리 확인
  python finalize_matching.py             # 실제 DB 반영
"""
import asyncio
import json
import re
import sys

from sqlalchemy import select

from app.db.database import async_session
from app.db.models import Product

ANALYSIS_FILE = "matching_analysis.json"
DIAGNOSIS_FILE = "unmatched_diagnosis.json"
RAW_FILE = "products_raw.json"

# 알려진 오매칭 강제 제외 목록 (사람이 직접 확인 후 제외하기로 결정한 항목)
KNOWN_BAD_MATCHES = {
    "GP-4301T",       # PFXGP4301TADW 와 글자만 우연히 겹침, 실제로는 다른 모델 체계
    "MR-J2S-700B",    # 본품 아닌 보드 단품(RG171)과 잘못 엮임
    "MR-J2S-40B",     # 본품 아닌 특정 사양품(PZ009U652)과 잘못 엮임
}


def is_safe_partial_match(model: str, store_name: str) -> bool:
    """모델명 뒤에 바로 영숫자가 이어붙지 않는 경우만 안전한 부분일치로 인정."""
    idx = store_name.upper().find(model.upper())
    if idx == -1:
        return False
    end = idx + len(model)
    if end >= len(store_name):
        return True
    next_char = store_name[end]
    # 다음 글자가 영문자/숫자면 위험 (다른 품목/사양코드일 가능성)
    return not next_char.isalnum()


async def main():
    dry_run = "--dry-run" in sys.argv

    with open(ANALYSIS_FILE, encoding="utf-8") as f:
        analysis = json.load(f)
    with open(DIAGNOSIS_FILE, encoding="utf-8") as f:
        diagnosis = json.load(f)
    with open(RAW_FILE, encoding="utf-8") as f:
        raw_items = json.load(f)

    # 모델명(맨앞 토큰) -> 상품 정보 1건 매핑 (1단계 단일매칭용)
    def extract_model_prefix(name: str) -> str:
        if not name:
            return ""
        positions = [p for p in (name.find(" "), name.find("("), name.find("[")) if p != -1]
        cut = min(positions) if positions else len(name)
        return name[:cut].strip().upper()

    groups: dict[str, list[dict]] = {}
    for item in raw_items:
        m = extract_model_prefix(item.get("name", ""))
        groups.setdefault(m, []).append(item)

    to_sync_true: list[tuple[str, dict]] = []   # (model_name, product_dict) - 매핑 반영 대상
    to_sync_false: list[str] = []                 # model_name 목록 - 연동 제외 대상
    excluded_bad: list[str] = []                   # 위험 패턴이라 건너뛴 것

    # 1) 단일매칭 43건
    for model in analysis["matched_unique"]:
        item = groups[model][0]
        to_sync_true.append((model, item))

    # 2) [C] 부분일치 중 안전 패턴만
    for entry in diagnosis["partial_match"]:
        model = entry["db_model"]
        if model in KNOWN_BAD_MATCHES:
            excluded_bad.append(model)
            continue
        if is_safe_partial_match(model, entry["store_name"]):
            to_sync_true.append((model, {
                "channel_product_no": entry["channel_product_no"],
                "origin_product_no": entry["origin_product_no"],
                "name": entry["store_name"],
            }))
        else:
            excluded_bad.append(model)

    # 3) [B] 표기차이 -> 알려진 위험 패턴이므로 전부 제외
    for entry in diagnosis["naming_mismatch"]:
        excluded_bad.append(entry["db_model"])

    # 4) [A] 미등록 -> 연동 제외 표시
    to_sync_false.extend(analysis["unmatched"])

    # 5) 중복 -> 수동 결정 대기, 아무 것도 안 함
    dup_models = list(analysis["matched_dup"])

    print(f"자동 반영 대상(origin_product_no 매핑): {len(to_sync_true)}건")
    print(f"연동 제외 표시 대상(inventory_sync_enabled=False): {len(to_sync_false)}건")
    print(f"위험 패턴으로 건너뜀: {len(excluded_bad)}건 -> {excluded_bad}")
    print(f"수동 결정 대기(중복): {len(dup_models)}건 -> {dup_models}")

    if dry_run:
        print("\ndry-run 모드: DB에 반영하지 않았습니다.")
        print("\n[반영 예정 매핑 샘플 (최대 10건)]")
        for model, item in to_sync_true[:10]:
            print(f"  {model} -> origin_product_no={item['origin_product_no']} ({item['name']})")
        return

    updated_count = 0
    flagged_count = 0

    async with async_session() as db:
        for model, item in to_sync_true:
            stmt = select(Product).where(Product.model_name == model)
            product = (await db.execute(stmt)).scalar_one_or_none()
            if product:
                product.smartstore_product_id = str(item["channel_product_no"])
                product.origin_product_no = str(item["origin_product_no"])
                product.inventory_sync_enabled = True
                updated_count += 1

        for model in to_sync_false:
            stmt = select(Product).where(Product.model_name == model)
            product = (await db.execute(stmt)).scalar_one_or_none()
            if product:
                product.inventory_sync_enabled = False
                flagged_count += 1

        await db.commit()

    print(f"\nDB 반영 완료: 매핑 {updated_count}건, 연동제외 표시 {flagged_count}건")


if __name__ == "__main__":
    asyncio.run(main())