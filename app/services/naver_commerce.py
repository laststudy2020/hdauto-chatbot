"""
네이버 커머스API 클라이언트 — 실시간 재고 조회 전용.

주의: app.config.Settings의 NAVER_CLIENT_ID/SECRET(네이버 검색 오픈API, 웹검색용)과는
완전히 별개의 API/인증체계입니다. 혼동하지 말 것.

인증 방식: OAuth2 Client Credentials Grant + bcrypt 서명
참고 문서: https://apicenter.commerce.naver.com/docs/commerce-api/current

──────────────────────────────────────────────
재고 조회 전략 (2026-07-02 확정)
──────────────────────────────────────────────
POST /external/v1/products/search 엔드포인트는 자유 텍스트 상품명 검색을
지원하지 않는다 (실측 확인됨: searchKeywordType이 CHANNEL_PRODUCT_NO,
ORIGIN_PRODUCT_NO, SELLER_MANAGEMENT_CODE, GROUP_PRODUCT_NO, SEARCH_ALL/ALL
등 "정확한 코드" 또는 "전체조회" 기준만 지원하며, 텍스트 키워드 필드는
어떤 이름으로 넣어도 무시되고 SEARCH_ALL과 동일한 전체 목록이 반환됨).

따라서 모델명 기반 재고조회는 다음 방식으로 구현함:
1. searchKeywordType=SEARCH_ALL로 전체 카탈로그(약 3,175건)를 페이지네이션
   조회해서 프로세스 메모리에 캐시 (TTL 있음, 기본 10분)
2. 캐시된 목록에서 모델명 부분일치로 클라이언트 사이드 검색
3. origin_product_no 사전 매핑(finalize_matching.py의 KNOWN_BAD_MATCHES 등)에
   더 이상 의존하지 않음 — 그 로직이 잘못되면 실제 재고가 있어도 "재고 없음"
   으로 응답하는 문제가 반복됐기 때문 (예: MR-J2S-40B 오매칭 건)

get_live_stock_quantity()는 origin_product_no가 확실한 경우(관리자 지정 조회
등)를 위해 남겨둠. 일반 챗봇 재고 조회 흐름에서는
search_stock_by_model_name()을 사용할 것.

──────────────────────────────────────────────
월요일 활성화 체크리스트
──────────────────────────────────────────────
1. .env에 NAVER_COMMERCE_CLIENT_ID / NAVER_COMMERCE_CLIENT_SECRET 채우기
2. 커머스API센터(apicenter.commerce.naver.com)에서 애플리케이션 API호출 IP
   등록 완료 (NAS 프록시 또는 Render Dedicated IP 등 확정된 IP)
3. settings.NAVER_COMMERCE_ENABLED = True 로 전환 (.env 또는 Render 환경변수)
4. 실제 모델명 1~2개로 search_stock_by_model_name() 단독 테스트 후 배포
5. 전체 카탈로그가 3,175건 규모라 캐시 TTL(_CATALOG_CACHE_TTL_SECONDS)을
   트래픽/피크시간 API 제한과 맞춰 조정할 것
──────────────────────────────────────────────
"""

import base64
import logging
import time
from datetime import datetime, timedelta

import bcrypt
import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

TOKEN_URL = "https://api.commerce.naver.com/external/v1/oauth2/token"
ORIGIN_PRODUCT_URL = "https://api.commerce.naver.com/external/v2/products/origin-products/{origin_product_no}"
PRODUCT_SEARCH_URL = "https://api.commerce.naver.com/external/v1/products/search"

# 전체 카탈로그 캐시 TTL (초). 너무 짧으면 API 호출이 잦아지고,
# 너무 길면 방금 등록한 신상품이 즉시 반영 안 됨.
_CATALOG_CACHE_TTL_SECONDS = 600  # 10분

# 프로세스 내 메모리 캐시 (서버 재시작 시 초기화됨)
_token_cache: dict = {"access_token": None, "expires_at": datetime.min}
_catalog_cache: dict = {"products": [], "fetched_at": datetime.min}


class NaverCommerceError(Exception):
    """네이버 커머스API 호출 관련 오류 (호출 측에서 DB 폴백 트리거용)"""


def _build_signature(client_id: str, client_secret: str, timestamp: int) -> str:
    """`client_id_timestamp` 문자열을 client_secret으로 bcrypt 해싱 후 base64 인코딩."""
    password = f"{client_id}_{timestamp}".encode("utf-8")
    hashed = bcrypt.hashpw(password, client_secret.encode("utf-8"))
    return base64.b64encode(hashed).decode("utf-8")


async def _get_access_token() -> str:
    """Access Token 발급/캐싱. 만료 1분 전이면 미리 재발급."""
    now = datetime.utcnow()
    if _token_cache["access_token"] and now < _token_cache["expires_at"] - timedelta(minutes=1):
        return _token_cache["access_token"]

    client_id = settings.NAVER_COMMERCE_CLIENT_ID
    client_secret = settings.NAVER_COMMERCE_CLIENT_SECRET
    if not client_id or not client_secret:
        raise NaverCommerceError("NAVER_COMMERCE_CLIENT_ID/SECRET이 설정되지 않았습니다.")

    timestamp = int(time.time() * 1000)
    signature = _build_signature(client_id, client_secret, timestamp)

    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "client_id": client_id,
                    "timestamp": timestamp,
                    "client_secret_sign": signature,
                    "grant_type": "client_credentials",
                    "type": "SELF",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        detail = ""
        if e.response is not None:
            try:
                detail = f" | 응답내용: {e.response.text}"
            except Exception:
                pass
        raise NaverCommerceError(f"토큰 발급 실패: {e}{detail}") from e

    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = now + timedelta(seconds=int(data.get("expires_in", 3000)))
    logger.info("네이버 커머스API 토큰 발급/갱신 완료")
    return _token_cache["access_token"]


async def _authorized_post(url: str, json_body: dict | None = None) -> dict:
    """토큰 첨부 POST 요청. 401이면 토큰 1회 재발급 후 재시도.
    400 등 그 외 실패는 응답 본문(JSON)을 포함해 NaverCommerceError로 올림.
    """
    token = await _get_access_token()
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
        resp = await client.post(url, json=json_body, headers={"Authorization": f"Bearer {token}"})

        if resp.status_code == 401:
            _token_cache["access_token"] = None
            token = await _get_access_token()
            resp = await client.post(url, json=json_body, headers={"Authorization": f"Bearer {token}"})

    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise NaverCommerceError(f"API 요청 실패 status={resp.status_code}: {detail}")

    return resp.json()


async def get_live_stock_quantity(origin_product_no: str) -> int:
    """원상품번호로 네이버 커머스API에서 실시간 재고수량(stockQuantity)을 조회.

    원상품번호가 확실할 때만 사용할 것. 일반 챗봇 재고 조회 흐름에서는
    search_stock_by_model_name()을 사용.
    """
    url = ORIGIN_PRODUCT_URL.format(origin_product_no=origin_product_no)

    try:
        token = await _get_access_token()
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})

            if resp.status_code == 401:
                _token_cache["access_token"] = None
                token = await _get_access_token()
                resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})

        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        raise NaverCommerceError(f"상품 조회 실패 (origin_product_no={origin_product_no}): {e}") from e

    stock = data.get("originProduct", {}).get("stockQuantity")
    if stock is None:
        raise NaverCommerceError(f"응답에 stockQuantity 없음 (origin_product_no={origin_product_no})")

    return int(stock)


async def _fetch_full_catalog() -> list[dict]:
    """
    searchKeywordType=SEARCH_ALL로 전체 판매중 상품을 페이지네이션 조회.
    products/search가 텍스트 키워드 검색을 지원하지 않으므로(실측 확인됨),
    전체를 가져와 캐싱한 뒤 이름 매칭은 클라이언트 사이드에서 수행한다.

    Returns:
        [{"name": str, "stock_quantity": int, "channel_product_no": str}, ...]
    """
    page_size = 100
    page = 1
    all_products: list[dict] = []

    while True:
        body = {
            "searchKeywordType": "SEARCH_ALL",
            "productStatusTypes": ["SALE"],
            "page": page,
            "size": page_size,
            "orderType": "NO",
        }
        data = await _authorized_post(PRODUCT_SEARCH_URL, json_body=body)
        contents = data.get("contents", [])
        if not contents:
            break

        for item in contents:
            for cp in item.get("channelProducts", []):
                all_products.append({
                    "name": cp.get("name", ""),
                    "stock_quantity": cp.get("stockQuantity", 0) or 0,
                    "channel_product_no": cp.get("channelProductNo"),
                    "status_type": cp.get("statusType"),
                })

        total_count = data.get("totalCount") or data.get("totalElements") or 0
        if page * page_size >= total_count or len(contents) < page_size:
            break
        page += 1

    logger.info(f"[카탈로그 캐시] 전체 상품 {len(all_products)}건 조회 완료")
    return all_products


async def _get_cached_catalog() -> list[dict]:
    """TTL 기반 전체 카탈로그 캐시. 만료됐으면 재조회."""
    now = datetime.utcnow()
    age = (now - _catalog_cache["fetched_at"]).total_seconds()

    if _catalog_cache["products"] and age < _CATALOG_CACHE_TTL_SECONDS:
        return _catalog_cache["products"]

    try:
        products = await _fetch_full_catalog()
    except NaverCommerceError:
        # 재조회 실패 시, 캐시가 있으면(설령 만료됐어도) 있는 걸로 버팀
        if _catalog_cache["products"]:
            logger.warning("[카탈로그 캐시] 재조회 실패, 만료된 캐시로 폴백")
            return _catalog_cache["products"]
        raise

    _catalog_cache["products"] = products
    _catalog_cache["fetched_at"] = now
    return products


async def search_stock_by_model_name(model_name: str) -> dict | None:
    """
    캐시된 전체 카탈로그에서 모델명이 상품명에 포함된 상품을 찾아
    재고를 합산 반환한다. (판매중 상품만 대상)

    Returns:
        {"quantity": int, "matched_name": str, "channel_product_no": str, "match_count": int}
        매칭 없으면 None

    Raises:
        NaverCommerceError: 카탈로그 조회 자체가 실패한 경우
    """
    catalog = await _get_cached_catalog()

    model_key = model_name.upper().replace(" ", "")
    matched = [
        p for p in catalog
        if model_key in p["name"].upper().replace(" ", "")
        and p.get("status_type") == "SALE"
    ]

    if not matched:
        return None

    matched.sort(key=lambda x: x["stock_quantity"], reverse=True)
    best = matched[0]
    total_stock = sum(m["stock_quantity"] for m in matched)

    return {
        "quantity": total_stock,
        "matched_name": best["name"],
        "channel_product_no": best["channel_product_no"],
        "match_count": len(matched),
    }