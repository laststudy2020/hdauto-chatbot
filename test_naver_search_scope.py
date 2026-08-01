"""네이버 검색 API에서 어떤 엔드포인트가 열려 있는지 전수 확인.

shop만 SE05인지, 아니면 여러 개가 막혔는지에 따라 콘솔에서 할 일이 달라진다.
- shop만 막힘  → 애플리케이션에 쇼핑 검색 권한이 없음 (정책/신청 문제)
- 전부 막힘    → 애플리케이션의 '검색' API 사용 설정 자체가 빠짐
- 401          → 자격증명이 유효하지 않음 (앱 삭제/키 재발급)

읽기 전용. 시크릿은 출력하지 않는다.

실행: python test_naver_search_scope.py
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

import httpx

from app.config import get_settings

settings = get_settings()

BASE = "https://openapi.naver.com/v1/search"
ENDPOINTS = [
    "shop", "blog", "news", "book", "image",
    "kin", "encyc", "cafearticle", "webkr", "doc", "local",
]

PAIRS = [
    ("NAVER_SHOPPING_*", settings.NAVER_SHOPPING_CLIENT_ID, settings.NAVER_SHOPPING_CLIENT_SECRET),
    ("NAVER_* (웹검색 폴백)", settings.NAVER_CLIENT_ID, settings.NAVER_CLIENT_SECRET),
]


async def probe(url: str, client_id: str, secret: str) -> str:
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=10.0) as client:
            resp = await client.get(
                url,
                params={"query": "서보드라이브", "display": 1},
                headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": secret},
            )
    except Exception as e:
        return f"호출 실패: {e}"

    if resp.is_success:
        return "OK"

    try:
        body = resp.json()
        code = body.get("errorCode", "?")
        msg = body.get("errorMessage", "")[:60]
    except Exception:
        code, msg = "?", resp.text.strip()[:60]
    return f"{resp.status_code} [{code}] {msg}"


async def main():
    for label, cid, sec in PAIRS:
        print(f"\n═══ {label} ═══")
        if not cid or not sec:
            print("  값이 비어 있어 건너뜀")
            continue

        ok_list, blocked = [], []
        for name in ENDPOINTS:
            status = await probe(f"{BASE}/{name}.json", cid, sec)
            marker = "✔" if status == "OK" else "✘"
            print(f"  {marker} {name:12} {status}")
            (ok_list if status == "OK" else blocked).append(name)

        print(f"\n  열림 {len(ok_list)}개: {ok_list}")
        print(f"  막힘 {len(blocked)}개: {blocked}")

        if not ok_list:
            print("  → 자격증명 자체가 무효하거나 '검색' API 사용 설정이 빠져 있다.")
        elif blocked == ["shop"]:
            print("  → 검색 API는 살아 있고 쇼핑만 막혔다. 앱에 쇼핑 검색 권한이 없다.")
        elif "shop" in blocked:
            print("  → 쇼핑 포함 일부가 막혔다. 콘솔에서 사용 API 설정을 확인할 것.")
        else:
            print("  → 쇼핑 검색은 정상이다.")


if __name__ == "__main__":
    asyncio.run(main())
