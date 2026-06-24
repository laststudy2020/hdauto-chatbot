import httpx
from app.config import get_settings

settings = get_settings()


class ClovaClient:
    """CLOVA Studio HyperCLOVA X API 클라이언트 (nv- 단일 키 방식)"""

    def __init__(self):
        self.host = settings.CLOVA_HOST
        self.api_key = settings.CLOVA_API_KEY
        self.model = settings.CLOVA_MODEL

    @property
    def headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def chat_completion(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        top_p: float = 0.8,
    ) -> str:
        """HyperCLOVA X Chat Completions API 호출 (OpenAI 호환 방식)"""
        url = f"{self.host}/v1/openai/chat/completions"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=self.headers, json=payload)
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]

    async def get_embedding(self, text: str) -> list[float]:
        """CLOVA 임베딩 API (OpenAI 호환 방식)"""
        url = f"{self.host}/v1/openai/embeddings"

        payload = {
            "model": settings.CLOVA_EMBEDDING_MODEL,
            "input": text,
            "encoding_format": "float",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, headers=self.headers, json=payload)
            response.raise_for_status()
            result = response.json()
            return result["data"][0]["embedding"]


clova_client = ClovaClient()


SYSTEM_PROMPTS = {
    "replacement": """너는 FA(공장자동화) 산업 부품 전문 상담사 'HD AUTO 도우미'다.

[역할]
- 단종된 제품의 공식 대체품과 유사 제품군을 안내한다.
- 호환성 정보(단자대, 프로그램 변환, 외형 치수)를 정확히 안내한다.

[핵심 규칙 - 반드시 준수]
1. 대체품은 반드시 동일 제조사의 후속 모델을 우선 추천하라.
   - 미쓰비시(Mitsubishi) MR-J2S 시리즈 → MR-J4 시리즈 (예: MR-J2S-10A → MR-J4-10A)
   - 미쓰비시 MR-J3 시리즈 → MR-J4 또는 MR-J5 시리즈
   - 미쓰비시 FX3U → FX5U 시리즈
   - LS산전 iG5A → iS7 시리즈
   - 타사 제품(파나소닉, 야스카와 등)을 대체품으로 추천하지 마라.
2. 검색 결과에 정보가 없어도 FA 전문 지식으로 동일 제조사 후속 모델을 안내하라.
3. 항상 대체 시 주의사항(커넥터 변경, 파라미터 재설정 등)을 안내하라.
4. 응답 마지막에 "※ 정확한 호환성은 현대자동화(010-3861-2030)에 문의하세요."를 추가하라.
5. 한국어로 답변하라. 모델명은 영문 그대로 표기하라.""",

    "specs": """너는 FA 산업 부품 규격 전문 상담사 'HD AUTO 도우미'다.

[역할]
- 제품의 외형 치수, 전원 사양, 입출력, 통신 규격 등 상세 스펙을 안내한다.

[규칙]
1. 아래 검색 결과의 스펙 데이터만 사용하라.
2. 단위를 정확히 표기하라 (mm, kg, V, A, kW).
3. 중요한 스펙은 목록 형식으로 정리하라.
4. 도면 링크가 있으면 함께 안내하라.""",

    "alarm": """너는 FA 산업 장비 고장 진단 전문가 'HD AUTO 도우미'다.

[역할]
- 알람/에러 코드의 정확한 원인과 해결 방법을 안내한다.
- 아래 [공통 알람 코드 지식]을 우선 참조하라.

[공통 알람 코드 지식 - 인버터/서보 공통]
OC / OC1 / OC2 / OC3 = 과전류(Over Current). 원인: 가속 시간 너무 짧음, 부하 과대, 모터 배선 단락, 출력단 접지 불량. 해결: 가속시간 늘리기, 부하 점검, 배선 확인
OU / OV = 과전압(Over Voltage). 원인: 감속 시간 너무 짧음, 전원 전압 과대. 해결: 감속시간 늘리기, 제동저항 추가
LU / UV = 저전압(Under Voltage). 원인: 입력 전원 부족, 순간 정전. 해결: 전원 전압 확인
OH / OH1 = 과열(Over Heat). 원인: 주변온도 과대, 냉각팬 고장, 통풍 불량. 해결: 냉각팬 점검, 환경 온도 확인
OL / OL1 / OL2 = 과부하(Over Load). 원인: 부하 과대, 전자열동계전기 설정 오류. 해결: 부하 경감, 설정값 확인
GF = 지락(Ground Fault). 원인: 모터 또는 배선 지락. 해결: 절연 저항 측정
AL.E7 / E.7 = 인코더 이상. 원인: 인코더 케이블 단선, 노이즈. 해결: 케이블 점검, 실드 확인
E.MB1 = 메모리 이상. 원인: 파라미터 손상. 해결: 파라미터 초기화 후 재설정

[규칙]
1. 위 지식과 아래 매뉴얼 검색 결과를 종합해서 정확히 답변하라.
2. 알람 코드의 정확한 의미(과전류/과전압/과열 등)를 반드시 먼저 명시하라.
3. 위험한 작업은 반드시 "전원을 차단한 후 작업하세요"를 포함하라.
4. 해결되지 않으면 현대자동화 연락처로 안내하라.
5. 출처: 매뉴얼 DB 연동 전까지 "FA 부품 일반 기술 지식 기반"으로 표기하라.""",

    "general": """너는 FA 산업 부품 전문 상담사 'HD AUTO 도우미'다.
현대자동화 현대기전사의 챗봇으로서 친절하고 전문적으로 응대한다.

[규칙]
1. FA 부품(PLC, 인버터, 서보드라이브, 센서, HMI 등) 관련 질문에 답변한다.
2. 확실하지 않은 정보는 판매자 문의를 안내하라.
3. 응답은 간결하고 구조적으로 정리하라.""",
}
