"""
네이버 검색 API 기반 웹 검색 서비스
DB에 없는 정보를 웹에서 검색해서 HyperCLOVA로 답변 생성
"""
import httpx
import logging
from app.config import get_settings
from app.core.clova import clova_client

logger = logging.getLogger(__name__)
settings = get_settings()


async def search_naver(query: str, display: int = 10) -> list[dict]:
    """네이버 검색 API 호출 - 블로그 + 카페 + 지식iN 검색"""
    if not settings.NAVER_CLIENT_ID or not settings.NAVER_CLIENT_SECRET:
        return []

    headers = {
        "X-Naver-Client-Id": settings.NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": settings.NAVER_CLIENT_SECRET,
    }

    results = []

    # 블로그 검색
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                "https://openapi.naver.com/v1/search/blog",
                headers=headers,
                params={"query": query, "display": display, "sort": "sim"}
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("items", []):
                    results.append({
                        "title": _strip_html(item.get("title", "")),
                        "description": _strip_html(item.get("description", "")),
                        "link": item.get("link", ""),
                        "source": "블로그"
                    })
        except Exception as e:
            logger.warning(f"블로그 검색 실패: {e}")

    return results[:10]


def _strip_html(text: str) -> str:
    """HTML 태그 제거"""
    import re
    return re.sub(r'<[^>]+>', '', text).strip()


async def search_and_answer(
    query: str,
    intent: str = "general",
    db_context: str = ""
) -> tuple[str, str]:
    """
    DB 우선 → 없으면 웹 검색 10개 → HyperCLOVA로 최적 답변 생성
    반환: (답변, 출처)
    """
    # 1) DB 컨텍스트가 있으면 DB 우선 답변
    if db_context:
        system_prompt = _get_system_prompt(intent)
        reply = await clova_client.chat_completion(
            system_prompt=system_prompt,
            user_message=f"[DB 검색 결과]\n{db_context}\n\n[질문]\n{query}",
            temperature=0.2,
        )
        return reply, "db"

    # 2) DB에 없으면 웹 검색
    search_query = _build_search_query(query, intent)
    web_results = await search_naver(search_query, display=10)

    if not web_results:
        # 웹 검색도 안 되면 HyperCLOVA 자체 지식으로 답변
        reply = await clova_client.chat_completion(
            system_prompt=_get_system_prompt(intent),
            user_message=(
                f"[참고]\nDB와 웹 검색 결과가 없습니다. "
                f"FA 부품 전문 지식으로 답변하되 불확실한 정보는 명시하세요.\n\n"
                f"[질문]\n{query}"
            ),
            temperature=0.3,
        )
        return reply, "clova_knowledge"

    # 3) 검색 결과 10개를 컨텍스트로 HyperCLOVA에 전달
    web_context = "\n\n".join([
        f"[출처 {i+1}: {r['source']}] {r['title']}\n{r['description']}"
        for i, r in enumerate(web_results[:10])
    ])

    reply = await clova_client.chat_completion(
        system_prompt=_get_web_search_prompt(intent),
        user_message=(
            f"[웹 검색 결과 10개]\n{web_context}\n\n"
            f"[질문]\n{query}\n\n"
            f"위 검색 결과들을 분석해서 신뢰도가 높은 정보를 중심으로 답변하세요."
        ),
        temperature=0.2,
        max_tokens=1024,
    )

    return reply, "web_search"


def _build_search_query(query: str, intent: str) -> str:
    """의도에 맞는 검색 쿼리 생성"""
    if intent == "alarm":
        return f"FA 인버터 서보 {query} 알람 원인 해결"
    elif intent == "replacement":
        return f"{query} 단종 대체품 호환 후속모델"
    elif intent == "specs":
        return f"{query} 규격 사양 치수 스펙"
    return f"FA 자동화 부품 {query}"


def _get_system_prompt(intent: str) -> str:
    from app.core.clova import SYSTEM_PROMPTS
    return SYSTEM_PROMPTS.get(intent, SYSTEM_PROMPTS["general"])


def _get_web_search_prompt(intent: str) -> str:
    base = _get_system_prompt(intent)
    return base + """

[웹 검색 결과 활용 규칙]
1. 제공된 검색 결과 10개를 분석해서 공통적으로 언급되는 내용을 신뢰도 높은 정보로 판단하라.
2. 단 1개의 출처만 언급하는 내용은 불확실하다고 표시하라.
3. 여러 출처에서 일치하는 정보를 중심으로 답변하라.
4. 답변 마지막에 "※ 웹 검색 기반 답변입니다. 공식 매뉴얼 확인을 권장합니다."를 추가하라."""
