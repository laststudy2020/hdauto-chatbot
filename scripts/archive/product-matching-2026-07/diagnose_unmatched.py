"""
matching_analysis.json의 unmatched(매칭 실패) 목록을 products_raw.json 전체와
느슨하게(loose) 재대조하여, 실제 원인이 다음 중 무엇인지 분류한다.

  A. 진짜 미등록  — 상품명 어디에도 모델명 비슷한 문자열조차 없음
  B. 표기차이 의심 — 하이픈/공백을 제거하고 비교하면 매칭되는 경우
     (예: DB "MR-J2S-40B1" vs 상품명 "MR-J2S-40B-1")
  C. 부분일치 의심 — 모델명이 상품명 '안에는' 포함되지만 맨 앞이 아니라서
     1단계 분석에서 놓친 경우

실행 (analyze_product_matching.py를 먼저 실행해 matching_analysis.json,
      products_raw.json이 있는 상태에서):
  python diagnose_unmatched.py
"""
import json
import re


def normalize(s: str) -> str:
    """비교용 정규화: 대문자화 + 하이픈/공백/언더스코어 제거."""
    return re.sub(r"[\s\-_]", "", s.upper())


def main():
    with open("matching_analysis.json", encoding="utf-8") as f:
        analysis = json.load(f)
    with open("products_raw.json", encoding="utf-8") as f:
        items = json.load(f)

    unmatched = analysis["unmatched"]
    print(f"매칭 실패 {len(unmatched)}건 재진단 시작\n")

    # 전체 상품명을 정규화해서 미리 준비
    norm_names = [(normalize(item.get("name", "")), item) for item in items]

    category_a, category_b, category_c = [], [], []

    for model in unmatched:
        norm_model = normalize(model)
        found_loose = None
        found_contains = None

        for norm_name, item in norm_names:
            if norm_name.startswith(norm_model):
                found_loose = item
                break

        if not found_loose:
            for norm_name, item in norm_names:
                if norm_model in norm_name:
                    found_contains = item
                    break

        if found_loose:
            category_b.append((model, found_loose))
        elif found_contains:
            category_c.append((model, found_contains))
        else:
            category_a.append(model)

    print("=" * 60)
    print(f"[A] 진짜 미등록 추정: {len(category_a)}건")
    print("=" * 60)
    for m in category_a:
        print(f"  - {m}")

    print(f"\n{'=' * 60}")
    print(f"[B] 표기차이 의심 (정규화 후 맨앞 일치): {len(category_b)}건")
    print("=" * 60)
    for model, item in category_b:
        print(f"  - DB: '{model}'  vs  스토어: '{item['name']}'  (채널번호={item['channel_product_no']})")

    print(f"\n{'=' * 60}")
    print(f"[C] 부분일치 의심 (모델명이 상품명 중간에 포함): {len(category_c)}건")
    print("=" * 60)
    for model, item in category_c:
        print(f"  - DB: '{model}'  vs  스토어: '{item['name']}'  (채널번호={item['channel_product_no']})")

    # 결과 저장
    with open("unmatched_diagnosis.json", "w", encoding="utf-8") as f:
        json.dump({
            "truly_unregistered": category_a,
            "naming_mismatch": [
                {"db_model": m, "channel_product_no": it["channel_product_no"],
                 "origin_product_no": it["origin_product_no"], "store_name": it["name"]}
                for m, it in category_b
            ],
            "partial_match": [
                {"db_model": m, "channel_product_no": it["channel_product_no"],
                 "origin_product_no": it["origin_product_no"], "store_name": it["name"]}
                for m, it in category_c
            ],
        }, f, ensure_ascii=False, indent=2)

    print(f"\n진단 결과 저장 완료 -> unmatched_diagnosis.json")
    print(
        f"\n요약: 미등록 추정 {len(category_a)}건 / "
        f"표기차이로 구제 가능 {len(category_b)}건 / "
        f"부분일치로 구제 가능 {len(category_c)}건"
    )


if __name__ == "__main__":
    main()