from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    APP_NAME: str = "HD AUTO 부품 도우미"
    DEBUG: bool = False

    # CLOVA Studio API
    CLOVA_API_KEY: str = ""
    CLOVA_HOST: str = "https://clovastudio.stream.ntruss.com"
    CLOVA_MODEL: str = "HCX-005"
    CLOVA_EMBEDDING_MODEL: str = "bge-m3"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./hdauto.db"

    # 실행 위치 표시용(local | render | nas). 로그와 / 응답에만 쓴다.
    # 비워두면 DATABASE_URL로 추정한다(sqlite면 local, 아니면 render).
    RUNTIME_ENV: str = ""
    # 매뉴얼 PDF 업로드 라우터. Render에서는 메모리가 모자라 껐고, 그 판정을
    # "DATABASE_URL이 sqlite인가"로 대신해왔다. NAS처럼 DB가 MariaDB이면서도
    # 메모리에 여유가 있는 환경이 생기면서 그 추정이 더는 성립하지 않아,
    # 명시적으로 켤 수 있는 스위치를 둔다(sqlite 로컬은 지금처럼 자동으로 켜짐).
    ENABLE_MANUAL_UPLOAD: bool = False

    # 네이버 검색 API (웹 검색용)
    NAVER_CLIENT_ID: str = ""
    NAVER_CLIENT_SECRET: str = ""

    # 네이버 커머스API (실시간 재고 조회용 - 검색API와는 별개의 API/인증체계)
    NAVER_COMMERCE_CLIENT_ID: str = ""
    NAVER_COMMERCE_CLIENT_SECRET: str = ""
    NAVER_COMMERCE_ENABLED: bool = False  # IP 등록/인증 완료 후 true로 전환

    # 재고/가격 설정
    DEFAULT_STOCK_THRESHOLD: int = 3
    PRICE_DIFF_THRESHOLD: float = 10.0

    # 현대자동화 위치 정보
    COMPANY_NAME: str = "HD AUTO 부품 도우미"
    COMPANY_ADDRESS: str = "부산광역시"
    COMPANY_PHONE: str = "010-3861-2030"
    COMPANY_LAT: float = 35.1796
    COMPANY_LNG: float = 129.0756
    COMPANY_HOURS: str = "평일 09:00~17:00"

    # 네이버 톡톡
    TALKTALK_AUTHORIZATION: str = ""
    TALKTALK_SECRET: str = ""
    # 웹훅 URL 자체에 붙이는 토큰. 톡톡이 서명 헤더를 보내지 않는 경우에도
    # 위조 요청을 막을 수 있도록 SECRET의 대안으로 둔다. 파트너센터에 등록하는
    # URL을 .../api/talktalk/webhook?k=<이 값> 으로 적으면 된다.
    TALKTALK_WEBHOOK_KEY: str = ""
    ADMIN_TALKTALK_USER_ID: str = ""  # 관리자 네이버 톡톡 user_id
    SLACK_WEBHOOK_URL: str = ""

    # 관리자 채팅 명령어 (대체품 등록 등)
    ADMIN_COMMAND_KEY: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",  # .env에 있지만 필드로 선언 안 된 값은 무시 (완전 차단보다 안전)
    )

    # 카카오 나에게 보내기
    KAKAO_REST_API_KEY: str = ""
    KAKAO_CLIENT_SECRET: str = ""
    KAKAO_REDIRECT_URI: str = "http://localhost:5000/oauth"
    # 수신자 자가등록 콜백. KAKAO_REDIRECT_URI(레거시 kakao_test 스크립트용)와 별개로
    # 두는 이유는 카카오 콘솔에 등록한 값과 글자 단위로 일치해야 하고, 하나를 고치면
    # 다른 흐름이 조용히 깨지기 때문이다.
    # 예: https://<배포도메인>/api/admin/recipients/callback
    KAKAO_RECIPIENT_REDIRECT_URI: str = ""

    # 네이버쇼핑 가격비교 (미설정 시 기존 NAVER_CLIENT_ID로 폴백)
    NAVER_SHOPPING_CLIENT_ID: str = ""
    NAVER_SHOPPING_CLIENT_SECRET: str = ""


@lru_cache()
def get_settings() -> Settings:
    return Settings()