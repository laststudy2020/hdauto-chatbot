import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.db.models import Base
from app.config import get_settings

settings = get_settings()

# asyncmy(MariaDB)만 connect_timeout을 지원 — aiosqlite(로컬)는 이 kwarg를 모르므로
# URL 스킴으로 분기. 이게 없으면 Tailscale 터널이 준비 안 된 상태에서 커넥션 시도가
# 무한 대기할 수 있다(코드리뷰 H4).
_IS_SQLITE = settings.DATABASE_URL.startswith("sqlite")
_connect_args = {} if _IS_SQLITE else {"connect_timeout": 10}

# aiosqlite는 NullPool로 붙어서 pool_size/max_overflow를 아예 받지 못한다(넘기면
# create_engine이 TypeError를 낸다). 로컬/드라이런 모드가 import 단계에서 죽지
# 않도록 connect_args와 같은 방식으로 분기한다.
_pool_args = {} if _IS_SQLITE else {"pool_size": 5, "max_overflow": 2}

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_recycle=280,      # 280초 이상 안 쓴 연결은 자동으로 폐기 후 재생성
    connect_args=_connect_args,
    **_pool_args,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _create_all():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def init_db():
    # DB connect_args의 connect_timeout이 개별 커넥션 시도는 막아주지만, 재시도 없이
    # 실패하면 앱 기동 자체가 멈추는 걸 막기 위해 전체 초기화에도 상한을 둔다.
    await asyncio.wait_for(_create_all(), timeout=30)


async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()