"""
매뉴얼 PDF 업로드 → 자동 처리 API
- 단건 업로드
- 다중 파일 일괄 업로드 (100개+)
- 처리 현황 조회
"""
import asyncio
import json
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List
from app.db.database import get_db, async_session
from app.db.models import AlarmCode, Product, Specification
from app.services.pdf_processor import process_manual_pdf
from app.core.clova import clova_client, SYSTEM_PROMPTS
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/manual", tags=["manual"])

# 처리 현황 저장 (메모리)
_job_status: dict = {}


@router.post("/upload", summary="단건 PDF 업로드")
async def upload_manual(
    file: UploadFile = File(...),
    manufacturer: str = Form(..., description="제조사명 (예: 미쓰비시)"),
    series: str = Form(..., description="시리즈명 (예: MELSERVO-J4)"),
    db: AsyncSession = Depends(get_db),
):
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(400, "PDF 파일만 업로드 가능합니다.")
    pdf_bytes = await file.read()
    result = await process_manual_pdf(pdf_bytes, manufacturer, series, db)
    return {
        "status": "완료",
        "file": file.filename,
        "pages": result["pages"],
        "products_found": len(result["products_found"]),
        "specs_saved": result["specs_saved"],
        "alarms_saved": result["alarms_saved"],
        "errors": result["errors"],
    }


@router.post("/upload/bulk", summary="다중 PDF 일괄 업로드 (100개+)")
async def upload_bulk(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(..., description="PDF 파일들 (여러 개 선택 가능)"),
    manufacturer: str = Form(..., description="제조사명"),
    series: str = Form(default="", description="시리즈명 (비워두면 파일명에서 자동 추출)"),
):
    """
    여러 PDF를 한 번에 업로드하면 백그라운드에서 순차 처리.
    처리 현황은 GET /api/manual/job/{job_id} 로 확인.
    """
    if len(files) > 200:
        raise HTTPException(400, "한 번에 최대 200개까지 업로드 가능합니다.")

    # 파일 내용을 미리 읽어둠
    file_data = []
    for f in files:
        if not f.filename.lower().endswith('.pdf'):
            continue
        content = await f.read()
        file_data.append({
            "filename": f.filename,
            "content": content,
            "series": series or _extract_series_from_filename(f.filename),
        })

    if not file_data:
        raise HTTPException(400, "PDF 파일이 없습니다.")

    # 작업 ID 생성
    import time
    job_id = f"job_{int(time.time())}"
    _job_status[job_id] = {
        "status": "processing",
        "total": len(file_data),
        "done": 0,
        "failed": 0,
        "results": [],
        "manufacturer": manufacturer,
    }

    # 백그라운드 처리
    background_tasks.add_task(
        _process_bulk_background,
        job_id, file_data, manufacturer
    )

    return {
        "job_id": job_id,
        "message": f"{len(file_data)}개 파일 처리 시작. 처리 현황은 아래 URL로 확인하세요.",
        "status_url": f"/api/manual/job/{job_id}",
        "total_files": len(file_data),
    }


async def _process_bulk_background(
    job_id: str, file_data: list, manufacturer: str
):
    """백그라운드에서 PDF 일괄 처리"""
    for i, fd in enumerate(file_data):
        try:
            async with async_session() as db:
                result = await process_manual_pdf(
                    pdf_bytes=fd["content"],
                    manufacturer=manufacturer,
                    series=fd["series"],
                    db=db,
                )
                _job_status[job_id]["results"].append({
                    "file": fd["filename"],
                    "series": fd["series"],
                    "pages": result["pages"],
                    "products": len(result["products_found"]),
                    "alarms": result["alarms_saved"],
                    "specs": result["specs_saved"],
                    "status": "완료",
                })
                _job_status[job_id]["done"] += 1
        except Exception as e:
            logger.error(f"[{job_id}] {fd['filename']} 처리 실패: {e}")
            _job_status[job_id]["results"].append({
                "file": fd["filename"],
                "status": "실패",
                "error": str(e),
            })
            _job_status[job_id]["failed"] += 1

        # API 과부하 방지
        await asyncio.sleep(1)

    _job_status[job_id]["status"] = "completed"
    logger.info(f"[{job_id}] 일괄 처리 완료: {_job_status[job_id]['done']}건 성공")


@router.get("/job/{job_id}", summary="일괄 처리 현황 조회")
async def get_job_status(job_id: str):
    """일괄 업로드 처리 현황 확인"""
    if job_id not in _job_status:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")

    job = _job_status[job_id]
    total = job["total"]
    done = job["done"]
    failed = job["failed"]
    pct = round((done + failed) / total * 100) if total > 0 else 0

    return {
        "job_id": job_id,
        "status": job["status"],
        "manufacturer": job["manufacturer"],
        "progress": f"{done + failed}/{total} ({pct}%)",
        "success": done,
        "failed": failed,
        "results": job["results"],
    }


def _extract_series_from_filename(filename: str) -> str:
    """파일명에서 시리즈명 자동 추출"""
    name = filename.replace('.pdf', '').replace('_', ' ').replace('-', ' ')
    keywords = {
        'J4': 'MELSERVO-J4', 'J2S': 'MELSERVO-J2S', 'J3': 'MELSERVO-J3',
        'FX5U': 'MELSEC-FX5U', 'FX3U': 'MELSEC-FX3U',
        'FR-E': 'FR-E Series', 'FR-A': 'FR-A Series',
        'iG5': 'SV-iG5A', 'iS7': 'SV-iS7',
        'XGB': 'XGB', 'XBC': 'XBC',
    }
    for key, series in keywords.items():
        if key.upper() in name.upper():
            return series
    return name[:30]


@router.get("/alarms/{manufacturer}", summary="등록된 알람코드 조회")
async def get_alarms(
    manufacturer: str,
    series: str = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AlarmCode).where(AlarmCode.manufacturer == manufacturer)
    if series:
        stmt = stmt.where(AlarmCode.product_series == series)
    result = await db.execute(stmt.limit(200))
    alarms = result.scalars().all()
    return [
        {
            "alarm_code": a.alarm_code,
            "alarm_name": a.alarm_name,
            "cause": a.cause,
            "solution": a.solution,
            "manual_page": a.manual_page,
            "series": a.product_series,
        }
        for a in alarms
    ]


@router.get("/stats", summary="매뉴얼 처리 통계")
async def get_manual_stats(db: AsyncSession = Depends(get_db)):
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
    from sqlalchemy import or_
    stmt = select(AlarmCode).where(
        or_(
            AlarmCode.alarm_code.ilike(f"%{query}%"),
            AlarmCode.alarm_name.ilike(f"%{query}%"),
            AlarmCode.cause.ilike(f"%{query}%"),
        )
    )
    if manufacturer:
        stmt = stmt.where(AlarmCode.manufacturer == manufacturer)
    result = await db.execute(stmt.limit(5))
    alarms = result.scalars().all()

    if not alarms:
        return {"answer": f"'{query}' 알람코드 정보가 아직 등록되지 않았습니다.", "source": "none"}

    context = "\n".join([
        f"[{a.alarm_code}] {a.alarm_name}\n원인: {a.cause}\n해결: {a.solution}\n출처: p.{a.manual_page}"
        for a in alarms
    ])
    answer = await clova_client.chat_completion(
        system_prompt=SYSTEM_PROMPTS["alarm"],
        user_message=f"[매뉴얼 검색 결과]\n{context}\n\n[질문]\n{query}",
        temperature=0.2,
    )
    return {"answer": answer, "source": "manual_db", "alarms_found": len(alarms)}
