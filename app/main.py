from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.db.database import init_db, async_session
from app.db.seed import seed_if_empty
from app.api.chatbot import router as chatbot_router
from app.api.products import router as products_router
from app.api.admin import router as admin_router
from app.api.recipients import router as recipients_router
from app.api.talktalk import router as talktalk_router
from app.api.webchat import router as webchat_router
from app.config import get_settings
import logging
import gc

logging.basicConfig(level=logging.INFO)
settings = get_settings()

IS_SQLITE = settings.DATABASE_URL.startswith("sqlite")
# DB 종류는 실행 위치의 대용물이었을 뿐이다(Render는 MariaDB, 로컬은 sqlite).
# NAS는 MariaDB를 쓰면서 로컬처럼 여유가 있어 그 대응이 깨진다. 명시값 우선.
RUNTIME_ENV = settings.RUNTIME_ENV or ("local" if IS_SQLITE else "render")
# 매뉴얼 업로드는 메모리를 많이 먹어 Render에서는 꺼둔다.
MANUAL_UPLOAD_ENABLED = IS_SQLITE or settings.ENABLE_MANUAL_UPLOAD


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logging.info("DB 초기화 완료")
    async with async_session() as db:
        await seed_if_empty(db)
    gc.collect()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    description="현대자동화 현대기전사 스마트스토어 챗봇 API v1.5",
    version="1.5.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chatbot_router)
app.include_router(products_router)
app.include_router(admin_router)
# 수신자 자가등록은 admin_router와 달리 X-Admin-Key 헤더를 걸 수 없다 (카톡 링크 클릭 +
# 카카오 리다이렉트로 들어오는 요청). 쿼리 key와 1회용 논스로 게이트한다.
app.include_router(recipients_router)
app.include_router(talktalk_router)
app.include_router(webchat_router)

if MANUAL_UPLOAD_ENABLED:
    from app.api.manual import router as manual_router
    app.include_router(manual_router)
    logging.info("매뉴얼 업로드 API 활성화 (%s)", RUNTIME_ENV)
else:
    logging.info("매뉴얼 업로드 API 비활성화 (%s - 메모리 절약)", RUNTIME_ENV)


@app.get("/", tags=["health"])
async def root():
    return {
        "app": settings.APP_NAME,
        "version": "1.5.0",
        "mode": RUNTIME_ENV,
        "manual_upload": MANUAL_UPLOAD_ENABLED,
        "chat_ui": "/chat",
        "docs": "/docs"
    }


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
