"""
PDF 처리 파이프라인 - 메모리 최적화 버전
페이지당 처리 후 즉시 메모리 해제
"""
import re
import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.models import Product, Specification, AlarmCode, ProductStatus
from app.core.clova import clova_client

logger = logging.getLogger(__name__)

# LS 인버터 매뉴얼은 고장표를 "트립"/"보호기능"으로 부른다 — alarm/error만
# 보면 정작 고장코드 표가 실린 페이지를 수집하지 못한다.
ALARM_KEYWORDS = [
    'alarm', 'error', 'fault', 'trip', 'al.', 'err',
    '알람', '에러', '이상', '트립', '고장', '경고', '보호기능',
]
SPEC_KEYWORDS = ['전원', '입력전압', '외형치수', '중량', 'dimensions', 'weight', 'voltage']

# 수집 상한. 이 파이프라인은 로컬에서만 돌기 때문에(Render 모드에선 매뉴얼
# 라우터가 아예 안 붙는다) Render 메모리에 맞춘 종전 값(10p/1200자)을 유지할
# 이유가 없다. 상한이 낮으면 고장표가 중간에서 잘려 조용히 유실된다.
MAX_ALARM_PAGES = 40
ALARM_TEXT_CHARS = 2500
MAX_MODELS_PER_PAGE = 30
MAX_MODELS_TOTAL = 60


async def process_manual_pdf(
    pdf_bytes: bytes,
    manufacturer: str,
    series: str,
    db: AsyncSession
) -> dict:
    results = {
        "manufacturer": manufacturer,
        "series": series,
        "pages": 0,
        "products_found": [],
        "specs_saved": 0,
        "alarms_saved": 0,
        "chunks_saved": 0,
        "errors": []
    }

    try:
        import gc
        import io
        import pdfplumber  # 함수 호출 시에만 로딩 (메모리 절약)

        alarm_pages = []
        spec_pages = []
        all_models = set()

        # 페이지별 순차 처리 (메모리 효율)
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            results["pages"] = len(pdf.pages)

            for i, page in enumerate(pdf.pages):
                try:
                    text = page.extract_text() or ""
                    if len(text) < 30:
                        continue

                    # 모델명 추출
                    models = _extract_model_names(text, manufacturer)
                    all_models.update(models)

                    text_lower = text.lower()

                    # 알람 페이지 수집
                    if len(alarm_pages) < MAX_ALARM_PAGES and any(k in text_lower for k in ALARM_KEYWORDS):
                        alarm_pages.append({"page": i+1, "text": text[:ALARM_TEXT_CHARS]})

                    # 스펙 페이지 수집 (최대 3페이지)
                    if len(spec_pages) < 3 and any(k in text_lower for k in SPEC_KEYWORDS):
                        spec_pages.append({"page": i+1, "text": text[:800]})

                    # 페이지 처리 후 즉시 메모리 해제
                    del text
                    if i % 10 == 0:
                        gc.collect()

                except Exception as e:
                    results["errors"].append(f"p.{i+1}: {str(e)[:50]}")

        # set이라 순서가 임의다 — 상한이 낮으면 어떤 모델이 잘릴지도 임의가 된다.
        results["products_found"] = sorted(all_models)[:MAX_MODELS_TOTAL]

        # 제품 DB 등록
        for model in results["products_found"]:
            await _save_product_if_new(model, manufacturer, series, db)

        # 스펙 추출 (페이지 1~2개만)
        if spec_pages:
            results["specs_saved"] = await _extract_specs(spec_pages[:2], manufacturer, series, db)

        # 알람코드 추출 (배치로 처리)
        if alarm_pages:
            results["alarms_saved"] = await _extract_alarms_batch(alarm_pages, manufacturer, series, db)

        results["chunks_saved"] = len(alarm_pages) + len(spec_pages)
        gc.collect()

    except Exception as e:
        results["errors"].append(f"PDF 처리 오류: {str(e)[:100]}")
        logger.error(f"PDF 처리 실패: {e}")

    await db.commit()
    return results


def _extract_model_names(text: str, manufacturer: str) -> list[str]:
    patterns = [
        r'MR-J[2-5][SA]?-\d+[A-Z]*',
        r'FX[35]U?-\d+[A-Z]{2}/[A-Z]{2}',
        r'FR-[A-Z]\d{3}-[\d\.]+[A-Z]*',
        # LS 신형 인버터 형명: LSLV0022S100-4EOFNM (S100/G100/H100/M100/L100)
        r'LSLV\d{4}[SGHML]100-[A-Z0-9]+',
        # 구형 iG5A: 예전 패턴(SV\d{3}[a-zA-Z]+[\-\d]+)은 [a-zA-Z]+가 숫자에서
        # 끊겨 SV008iG5A-4를 'SV008iG5'로 잘라 등록했다.
        r'SV\d{3}[A-Za-z0-9]*(?:-\d+)?',
        r'XB[MCG]-[A-Z]{2}\d+[A-Z]',
    ]
    found = set()
    for pattern in patterns:
        for m in re.findall(pattern, text, re.IGNORECASE):
            if len(m) >= 5:
                found.add(m.upper())
    return sorted(found)[:MAX_MODELS_PER_PAGE]


async def _save_product_if_new(model: str, manufacturer: str, series: str, db: AsyncSession):
    existing = await db.execute(select(Product).where(Product.model_name == model))
    if existing.scalar_one_or_none():
        return
    category = _infer_category(model, series)
    db.add(Product(
        model_name=model, series=series,
        manufacturer=manufacturer, category=category,
        status=ProductStatus.ACTIVE,
    ))


def _infer_category(model: str, series: str) -> str:
    t = f"{model} {series}".upper()
    if any(k in t for k in ['MR-J', 'SERVO']): return 'servo'
    if any(k in t for k in ['FX', 'XB', 'PLC']): return 'PLC'
    if any(k in t for k in ['FR-', 'IG5', 'IS7', 'LSLV',
                            'S100', 'G100', 'H100', 'M100', 'L100']):
        return 'inverter'
    if any(k in t for k in ['GT', 'HMI', 'GP']): return 'HMI'
    return 'FA'


async def _extract_specs(pages: list, manufacturer: str, series: str, db: AsyncSession) -> int:
    combined = "\n".join(f"[p.{p['page']}]\n{p['text']}" for p in pages)
    try:
        resp = await clova_client.chat_completion(
            system_prompt="FA 부품 스펙 추출 전문가. JSON만 응답. 없는 항목은 null.",
            user_message=f"제조사:{manufacturer} 시리즈:{series}\n{combined}\n\n"
                        f'{{"model_name":"","dimension_w":null,"dimension_h":null,"dimension_d":null,'
                        f'"weight_kg":null,"input_voltage":"","io_points":"","comm_protocol":"","operating_temp":""}}',
            temperature=0.1, max_tokens=300,
        )
        clean = re.sub(r'```json|```', '', resp).strip()
        data = json.loads(clean)
        if data.get("model_name"):
            prod = (await db.execute(
                select(Product).where(Product.model_name.ilike(f"%{data['model_name']}%"))
            )).scalars().first()
            if prod:
                existing = (await db.execute(
                    select(Specification).where(Specification.product_id == prod.id)
                )).scalar_one_or_none()
                if not existing:
                    db.add(Specification(
                        product_id=prod.id,
                        dimension_w=data.get("dimension_w"),
                        dimension_h=data.get("dimension_h"),
                        dimension_d=data.get("dimension_d"),
                        weight_kg=data.get("weight_kg"),
                        input_voltage=data.get("input_voltage"),
                        io_points=data.get("io_points"),
                        comm_protocol=data.get("comm_protocol"),
                        operating_temp=data.get("operating_temp"),
                    ))
                    return 1
    except Exception as e:
        logger.warning(f"스펙 추출 실패: {e}")
    return 0


async def _alarm_exists(
    db: AsyncSession, manufacturer: str, series: str, alarm_code: str
) -> bool:
    """이미 등록된 알람코드인가.

    시리즈까지 봐야 한다. 제조사+코드만 보면 LS처럼 계열 간 코드 표기를
    공유하는 제조사에서 신규 시리즈 매뉴얼을 넣을 때, 기존 시리즈(SV-iG5A)에
    같은 코드가 있다는 이유로 전부 스킵돼 새 시리즈 행이 하나도 안 생긴다.
    """
    row = (await db.execute(
        select(AlarmCode).where(
            AlarmCode.manufacturer == manufacturer,
            AlarmCode.product_series == series,
            AlarmCode.alarm_code == alarm_code,
        )
    )).scalar_one_or_none()
    return row is not None


async def _extract_alarms_batch(pages: list, manufacturer: str, series: str, db: AsyncSession) -> int:
    """알람 페이지를 3개씩 묶어서 배치 처리"""
    count = 0
    for i in range(0, len(pages), 3):
        batch = pages[i:i+3]
        combined = "\n".join(f"[p.{p['page']}]\n{p['text']}" for p in batch)
        try:
            resp = await clova_client.chat_completion(
                system_prompt="FA 부품 알람코드 추출 전문가. JSON 배열만 응답.",
                user_message=f"제조사:{manufacturer} 시리즈:{series}\n{combined}\n\n"
                            f'[{{"alarm_code":"","alarm_name":"","cause":"","solution":"","manual_page":""}}]',
                temperature=0.1, max_tokens=800,
            )
            clean = re.sub(r'```json|```', '', resp).strip()
            if not clean.startswith('['): continue
            alarms = json.loads(clean)
            for a in alarms:
                if not a.get("alarm_code"): continue
                if not await _alarm_exists(db, manufacturer, series, a["alarm_code"]):
                    db.add(AlarmCode(
                        manufacturer=manufacturer,
                        product_series=series,
                        alarm_code=a.get("alarm_code",""),
                        alarm_name=a.get("alarm_name",""),
                        cause=a.get("cause",""),
                        solution=a.get("solution",""),
                        manual_page=str(a.get("manual_page","")),
                        manual_filename=f"{manufacturer}_{series}.pdf",
                    ))
                    count += 1
        except Exception as e:
            logger.warning(f"알람 배치 추출 실패: {e}")
    return count
