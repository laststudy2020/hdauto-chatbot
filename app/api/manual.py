"""
매뉴얼 PDF 업로드 → 자동 처리 API
"""
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.db.database import get_db
from app.db.models import AlarmCode, Product, Specification
from app.services.pdf_processor import process_manual_pdf
from app.core.clova import clova_client, SYSTEM_PROMPTS

router = APIRouter(prefix="/api/manual", tags=["manual"])


@router.post("/upload", summary="제조사 매뉴얼 PDF 업로드 → 자동 처리")
async def upload_manual(
    file: UploadFile = File(..., description="제조사 공식 매뉴얼 PDF"),
    manufacturer: str = Form(..., description="제조사명 (예: 미쓰비시, LS산전)"),
    series: str = Form(..., description="시리즈명 (예: MELSERVO-J4, SV-iG5A)"),
    db: AsyncSession = Depends(get_db),
):
    """
    PDF를 업로드하면 자동으로:
    1. 모델명 추출 → products 테이블 등록
    2. 스펙/사이즈 추출 → specifications 테이블 등록
    3. 알람코드 추출 → alarm_codes 테이블 등록
    4. 텍스트 청킹 → RAG 검색 준비
    """
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(400, "PDF 파일만 업로드 가능합니다.")

    if file.size and file.size > 50 * 1024 * 1024:
        raise HTTPException(400, "파일 크기는 50MB 이하여야 합니다.")

    pdf_bytes = await file.read()

    result = await process_manual_pdf(
        pdf_bytes=pdf_bytes,
        manufacturer=manufacturer,
        series=series,
        db=db,
    )

    return {
        "status": "완료",
        "file": file.filename,
        "manufacturer": manufacturer,
        "series": series,
        "pages_processed": result["pages"],
        "products_found": len(result["products_found"]),
        "product_list": result["products_found"][:10],
        "specs_saved": result["specs_saved"],
        "alarms_saved": result["alarms_saved"],
        "chunks_indexed": result["chunks_saved"],
        "errors": result["errors"],
    }


@router.get("/alarms/{manufacturer}", summary="등록된 알람코드 조회")
async def get_alarms(
    manufacturer: str,
    series: str = None,
    db: AsyncSession = Depends(get_db),
):
    """등록된 알람코드 목록 조회"""
    stmt = select(AlarmCode).where(
        AlarmCode.manufacturer == manufacturer
    )
    if series:
        stmt = stmt.where(AlarmCode.product_series == series)
    result = await db.execute(stmt.limit(100))
    alarms = result.scalars().all()
    return [
        {
            "alarm_code": a.alarm_code,
            "alarm_name": a.alarm_name,
            "cause": a.cause,
            "solution": a.solution,
            "manual_page": a.manual_page,
        }
        for a in alarms
    ]


@router.get("/stats", summary="매뉴얼 처리 통계")
async def get_manual_stats(db: AsyncSession = Depends(get_db)):
    """등록된 알람코드 및 스펙 통계"""
    alarm_count = (await db.execute(select(func.count(AlarmCode.id)))).scalar()
    spec_count = (await db.execute(select(func.count(Specification.id)))).scalar()
    product_count = (await db.execute(select(func.count(Product.id)))).scalar()

    manufacturers = await db.execute(
        select(AlarmCode.manufacturer, func.count(AlarmCode.id))
        .group_by(AlarmCode.manufacturer)
    )

    return {
        "total_products": product_count,
        "specs_registered": spec_count,
        "alarm_codes": alarm_count,
        "by_manufacturer": [
            {"manufacturer": m, "alarm_count": c}
            for m, c in manufacturers.all()
        ],
    }


@router.post("/alarm/search", summary="알람코드 RAG 검색")
async def search_alarm(
    query: str,
    manufacturer: str = None,
    db: AsyncSession = Depends(get_db),
):
    """알람코드 검색 + HyperCLOVA 답변 생성"""
    # DB에서 관련 알람 검색
    stmt = select(AlarmCode).where(
        AlarmCode.alarm_code.ilike(f"%{query}%") |
        AlarmCode.alarm_name.ilike(f"%{query}%") |
        AlarmCode.cause.ilike(f"%{query}%")
    )
    if manufacturer:
        stmt = stmt.where(AlarmCode.manufacturer == manufacturer)
    result = await db.execute(stmt.limit(5))
    alarms = result.scalars().all()

    if not alarms:
        return {"answer": f"'{query}' 알람코드 정보가 아직 등록되지 않았습니다.\n매뉴얼 PDF를 업로드해 주세요.", "source": "none"}

    context = "\n".join([
        f"[{a.alarm_code}] {a.alarm_name}\n원인: {a.cause}\n해결: {a.solution}\n출처: {a.manual_filename} p.{a.manual_page}"
        for a in alarms
    ])

    answer = await clova_client.chat_completion(
        system_prompt=SYSTEM_PROMPTS["alarm"],
        user_message=f"[매뉴얼 검색 결과]\n{context}\n\n[질문]\n{query} 알람에 대해 알려주세요.",
        temperature=0.2,
    )

    return {
        "answer": answer,
        "source": "manual_db",
        "alarms_found": len(alarms),
    }
