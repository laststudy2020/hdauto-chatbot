"""
네이버 커머스API 단독 테스트 — MR-J2S-70A 재고 직접 조회.
DB나 챗봇 로직과 완전히 분리해서 API 자체가 작동하는지 확인.

실행:
  python test_commerce_api.py
"""
import asyncio
import httpx
import base64
import time
import bcrypt
from dotenv import load_dotenv
import os

load_dotenv()

CLIENT_ID = os.getenv("NAVER_COMMERCE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("NAVER_COMMERCE_CLIENT_SECRET", "")
TOKEN_URL = "https://api.commerce.naver.com/external/v1/oauth2/token"
ORIGIN_PRODUCT_URL = "https://api.commerce.naver.com/external/v2/products/origin-products/{}"

# MR-J2S-70A의 원상품번호 (DB에 저장된 값)
TEST_ORIGIN_PRODUCT_NO = "13582221844"


def build_signature(client_id: str, client_secret: str, timestamp: int) -> str:
    password = f"{client_id}_{timestamp}".encode("utf-8")
    hashed = bcrypt.hashpw(password, client_secret.encode("utf-8"))
    return base64.b64encode(hashed).decode("utf-8")


async def main():
    print(f"CLIENT_ID: {CLIENT_ID[:10]}..." if CLIENT_ID else "CLIENT_ID: 없음!")
    print(f"CLIENT_SECRET: {CLIENT_SECRET[:10]}..." if CLIENT_SECRET else "CLIENT_SECRET: 없음!")
    print()

    if not CLIENT_ID or not CLIENT_SECRET:
        print("❌ .env에 NAVER_COMMERCE_CLIENT_ID/SECRET이 없습니다.")
        return

    # 1) 토큰 발급
    print("1) 토큰 발급 시도...")
    timestamp = int(time.time() * 1000)
    signature = build_signature(CLIENT_ID, CLIENT_SECRET, timestamp)

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": CLIENT_ID,
                "timestamp": timestamp,
                "client_secret_sign": signature,
                "grant_type": "client_credentials",
                "type": "SELF",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        print(f"   status: {resp.status_code}")
        if resp.status_code != 200:
            print(f"   ❌ 토큰 발급 실패: {resp.text}")
            return

        token_data = resp.json()
        token = token_data.get("access_token", "")
        print(f"   ✅ 토큰 발급 성공: {token[:20]}...")
        print()

        # 2) 원상품 재고 조회
        print(f"2) 원상품 재고 조회 (origin_product_no={TEST_ORIGIN_PRODUCT_NO})...")
        url = ORIGIN_PRODUCT_URL.format(TEST_ORIGIN_PRODUCT_NO)
        resp2 = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        print(f"   status: {resp2.status_code}")

        if resp2.status_code != 200:
            print(f"   ❌ 상품 조회 실패: {resp2.text[:300]}")
            return

        data = resp2.json()
        stock = data.get("originProduct", {}).get("stockQuantity")
        name = data.get("originProduct", {}).get("name", "")
        print(f"   ✅ 상품명: {name}")
        print(f"   ✅ 재고수량(stockQuantity): {stock}")
        print()

        if stock is None:
            print("❌ stockQuantity 없음 — 응답 구조 확인 필요")
            print(f"   응답 키: {list(data.get('originProduct', {}).keys())}")
        elif stock == 0:
            print("ℹ️  재고 0 — 스마트스토어에 실제 재고가 없는 상태입니다.")
            print("   챗봇이 '재고 없음'을 출력하는 게 맞습니다.")
        else:
            print(f"✅ 재고 {stock}개 확인 — API 정상 작동 중입니다.")


if __name__ == "__main__":
    asyncio.run(main())