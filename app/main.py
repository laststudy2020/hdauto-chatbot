from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.db.database import init_db
from app.api.chatbot import router as chatbot_router
from app.api.products import router as products_router
from app.api.admin import router as admin_router
from app.config import get_settings
import logging

logging.basicConfig(level=logging.INFO)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logging.info("DB 초기화 완료")
    yield


app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "현대자동화 현대기전사 스마트스토어 챗봇 API\n\n"
        "단종 대체품 | 규격 조회 | 고장 알람 진단 | "
        "위치 안내 | 재고 알림 | 단가 비교"
    ),
    version="1.1.0",
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


@app.get("/", tags=["health"])
async def root():
    return {"app": settings.APP_NAME, "version": "1.1.0", "docs": "/docs"}


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
