from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.db.database import init_db, async_session
from app.db.seed import seed_if_empty
from app.api.chatbot import router as chatbot_router
from app.api.products import router as products_router
from app.api.admin import router as admin_router
from app.api.talktalk import router as talktalk_router
from app.api.webchat import router as webchat_router
from app.config import get_settings
import logging
import gc

logging.basicConfig(level=logging.INFO)
settings = get_settings()

# PDF 처리 라우터는 로컬 모드일 때만 활성화
IS_LOCAL = settings.DATABASE_URL.startswith("sqlite")


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
app.include_router(talktalk_router)
app.include_router(webchat_router)

# 매뉴얼 업로드는 로컬에서만 활성화 (Render 메모리 절약)
if IS_LOCAL:
    from app.api.manual import router as manual_router
    app.include_router(manual_router)
    logging.info("매뉴얼 업로드 API 활성화 (로컬 모드)")
else:
    logging.info("매뉴얼 업로드 API 비활성화 (Render 모드 - 로컬에서 처리)")


@app.get("/", tags=["health"])
async def root():
    return {
        "app": settings.APP_NAME,
        "version": "1.5.0",
        "mode": "local" if IS_LOCAL else "render",
        "manual_upload": IS_LOCAL,
        "chat_ui": "/chat",
        "docs": "/docs"
    }


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
