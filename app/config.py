from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    APP_NAME: str = "HD AUTO 부품 도우미"
    DEBUG: bool = True

    # CLOVA Studio API (nv- 단일 키 방식)
    CLOVA_API_KEY: str = ""
    CLOVA_HOST: str = "https://clovastudio.stream.ntruss.com"
    CLOVA_MODEL: str = "HCX-005"
    CLOVA_EMBEDDING_MODEL: str = "bge-m3"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./hdauto.db"

    # Qdrant Vector DB (Phase 2에서 사용)
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION: str = "fa_products"

    # 재고 알림 임계값
    DEFAULT_STOCK_THRESHOLD: int = 3

    # 경쟁사 가격 차이율 임계값 (%)
    PRICE_DIFF_THRESHOLD: float = 10.0

    # 현대자동화 위치 정보
    COMPANY_NAME: str = "HD AUTO 부품 도우미"
    COMPANY_ADDRESS: str = "부산광역시"
    COMPANY_PHONE: str = "051-000-0000"
    COMPANY_LAT: float = 35.1796
    COMPANY_LNG: float = 129.0756
    COMPANY_HOURS: str = "평일 09:00~18:00"

    # 경쟁사 가격 차이율 임계값 (%)
    PRICE_DIFF_THRESHOLD: float = 10.0

    # 네이버 톡톡
    TALKTALK_AUTHORIZATION: str = ""
    TALKTALK_SECRET: str = ""
    SLACK_WEBHOOK_URL: str = ""

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
