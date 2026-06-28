from pydantic_settings import BaseSettings
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
    COMPANY_PHONE: str = "051-000-0000"
    COMPANY_LAT: float = 35.1796
    COMPANY_LNG: float = 129.0756
    COMPANY_HOURS: str = "평일 09:00~18:00"

    # 네이버 톡톡
    TALKTALK_AUTHORIZATION: str = ""
    TALKTALK_SECRET: str = ""
    SLACK_WEBHOOK_URL: str = ""

    # 관리자 채팅 명령어 (대체품 등록 등)
    ADMIN_COMMAND_KEY: str = ""

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()