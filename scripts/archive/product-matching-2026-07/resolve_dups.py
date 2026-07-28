"""
중복 4건의 후보 목록을 products_raw.json에서 추출해서 출력.
가장 최근 채널상품번호(숫자 가장 큰 것)를 대표로 선정.
"""
import json

TARGET_MODELS = ["MR-J2S-70A", "MR-J2S-700A", "XBM-DN32S", "SV075IG5A-4"]

with open("products_raw.json", encoding="utf-8") as f:
    items = json.load(f)

def extract_prefix(name):
    if not name:
        return ""
    positions = [p for p in (name.find(" "), name.find("("), name.find("[")) if p != -1]
    cut = min(positions) if positions else len(name)
    return name[:cut].strip().upper()

results = {}
for item in items:
    prefix = extract_prefix(item.get("name", ""))
    if prefix in TARGET_MODELS:
        results.setdefault(prefix, []).append(item)

print("=== 중복 4건 후보 목록 ===\n")
sql_lines = []
for model in TARGET_MODELS:
    candidates = results.get(model, [])
    if not candidates:
        print(f"[{model}] 후보 없음\n")
        continue
    
    # 채널상품번호 가장 큰 것을 대표로 선정
    best = max(candidates, key=lambda x: int(x.get("channel_product_no", 0)))
    
    print(f"[{model}] {len(candidates)}건 후보")
    for c in candidates:
        marker = " ← 선택" if c == best else ""
        print(f"  채널={c['channel_product_no']} | 원상품={c['origin_product_no']} | {c['name']}{marker}")
    
    sql_lines.append(
        f"UPDATE products SET "
        f"origin_product_no = '{best['origin_product_no']}', "
        f"smartstore_product_id = '{best['channel_product_no']}', "
        f"inventory_sync_enabled = 1 "
        f"WHERE model_name = '{model}';"
    )
    print()

print("\n=== 실행할 SQL ===")
for line in sql_lines:
    print(line)