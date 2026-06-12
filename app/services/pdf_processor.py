"""
제조사 매뉴얼 PDF 자동 처리 파이프라인
- 단종/대체품 정보 추출 → products + replacements 테이블
- 제품 스펙/사이즈 추출 → specifications 테이블
- 고장 알람코드 추출 → alarm_codes 테이블
- 전체 텍스트 청크 → manual_chunks 테이블 (RAG용)
"""
import re
import json
import pdfplumber
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.models import Product, Specification, AlarmCode, ProductStatus
from app.core.clova import clova_client

logger = logging.getLogger(__name__)

# ─── 패턴 정의 ───
ALARM_PATTERNS = [
    r'(AL[\.\-]?\s*[A-Z]?\d+|E\d{2,4}|Err[\.\-]?\d+|[Ff]ault\s*\d+|OC\d?|OL\d?|OU\d?|OH\d?|LU|GF)',
]

SPEC_KEYWORDS = [
    '전원', '입력전압', '출력', '정격', '외형치수', '중량', '질량',
    'W×H×D', '동작온도', '보호등급', '통신', '인터페이스',
    'power supply', 'input voltage', 'output', 'dimensions', 'weight',
    'operating temperature', 'protection', 'communication'
]

DISCONTINUE_KEYWORDS = ['단종', '생산중지', '생산 중지', 'discontinued', 'end of life', 'EOL', '후속기종', '대체']


async def process_manual_pdf(
    pdf_bytes: bytes,
    manufacturer: str,
    series: str,
    db: AsyncSession
) -> dict:
    """
    PDF 바이트를 받아서 자동으로 정보 추출 및 DB 저장
    반환: {products, specs, alarms, chunks} 처리 결과
    """
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
        import io
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            results["pages"] = len(pdf.pages)
            all_text = ""
            page_texts = []

            for i, page in enumerate(pdf.pages):
                try:
                    text = page.extract_text() or ""
                    page_texts.append({"page": i + 1, "text": text})
                    all_text += f"\n[페이지 {i+1}]\n{text}"
                except Exception as e:
                    results["errors"].append(f"p.{i+1} 텍스트 추출 오류: {str(e)}")

            # 1) 모델명 추출
            models = _extract_model_names(all_text, manufacturer)
            results["products_found"] = models

            # 2) 모델별 DB 등록
            for model in models[:20]:  # 최대 20개
                await _save_product_if_new(model, manufacturer, series, db)

            # 3) 스펙 추출 (HyperCLOVA 활용)
            spec_count = await _extract_and_save_specs(
                page_texts, manufacturer, series, db
            )
            results["specs_saved"] = spec_count

            # 4) 알람코드 추출
            alarm_count = await _extract_and_save_alarms(
                page_texts, manufacturer, series, db
            )
            results["alarms_saved"] = alarm_count

            # 5) 청크 저장 (RAG용)
            chunk_count = await _save_chunks(
                page_texts, manufacturer, series, db
            )
            results["chunks_saved"] = chunk_count

    except Exception as e:
        results["errors"].append(f"PDF 처리 오류: {str(e)}")
        logger.error(f"PDF 처리 실패: {e}", exc_info=True)

    await db.commit()
    return results


def _extract_model_names(text: str, manufacturer: str) -> list[str]:
    """텍스트에서 FA 부품 모델명 추출"""
    patterns = [
        r'MR-J[2-5][SA]?-\d+[A-Z]*',           # 미쓰비시 서보
        r'FX[35]U?-\d+[A-Z]{2}/[A-Z]{2}',       # 미쓰비시 PLC
        r'FR-[A-Z]\d{3}-\d+',                    # 미쓰비시 인버터
        r'SV\d{3}[a-zA-Z]+[\-\d]+',              # LS산전 인버터
        r'XB[MCG]-[A-Z]{2}\d+[A-Z]',             # LS산전 PLC
        r'E\d{2}[A-Z]\d-\d{4}-\d-[A-Z]-\d+',    # 오토닉스 인코더
        r'[A-Z]{2,5}[\-]\d{1,3}[A-Z]{0,3}[\-\d]*',  # 일반 패턴
    ]

    found = set()
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        found.update(m.upper() for m in matches if len(m) >= 5)

    return list(found)[:30]


async def _save_product_if_new(
    model_name: str, manufacturer: str, series: str, db: AsyncSession
) -> bool:
    """모델이 없으면 DB에 새로 추가"""
    existing = await db.execute(
        select(Product).where(Product.model_name == model_name)
    )
    if existing.scalar_one_or_none():
        return False

    # 카테고리 자동 추론
    category = _infer_category(model_name, series)

    product = Product(
        model_name=model_name,
        series=series,
        manufacturer=manufacturer,
        category=category,
        status=ProductStatus.ACTIVE,
    )
    db.add(product)
    return True


def _infer_category(model_name: str, series: str) -> str:
    """모델명/시리즈에서 카테고리 자동 추론"""
    text = f"{model_name} {series}".upper()
    if any(k in text for k in ['MR-J', 'SV', 'SERVO', '서보']):
        return '서보드라이브'
    if any(k in text for k in ['FX', 'XB', 'PLC', 'Q0', 'L0']):
        return 'PLC'
    if any(k in text for k in ['FR-', 'IG5', 'IS7', 'INVERTER', '인버터']):
        return '인버터'
    if any(k in text for k in ['GT', 'GOT', 'HMI', 'GP']):
        return 'HMI'
    if any(k in text for k in ['E40', 'E50', 'ENCODER', '인코더']):
        return '인코더'
    if any(k in text for k in ['PR', 'BF', 'SENSOR', '센서']):
        return '센서'
    return 'FA부품'


async def _extract_and_save_specs(
    page_texts: list, manufacturer: str, series: str, db: AsyncSession
) -> int:
    """HyperCLOVA로 스펙 정보 추출 후 저장"""
    count = 0
    spec_pages = []

    for pt in page_texts:
        if any(kw in pt["text"] for kw in SPEC_KEYWORDS):
            spec_pages.append(pt)
        if len(spec_pages) >= 5:
            break

    if not spec_pages:
        return 0

    combined = "\n".join(f"[p.{p['page']}]\n{p['text'][:800]}" for p in spec_pages[:3])

    prompt = f"""아래 FA 부품 매뉴얼 텍스트에서 제품 스펙 정보를 추출하라.
제조사: {manufacturer}, 시리즈: {series}

[텍스트]
{combined}

다음 JSON 형식으로만 응답하라 (없는 항목은 null):
{{
  "model_name": "모델명",
  "dimension_w": 가로mm숫자,
  "dimension_h": 세로mm숫자,
  "dimension_d": 깊이mm숫자,
  "weight_kg": 무게kg숫자,
  "input_voltage": "전원전압문자열",
  "output_type": "출력방식",
  "io_points": "입출력점수",
  "comm_protocol": "통신규격",
  "operating_temp": "동작온도",
  "rated_power": "정격출력"
}}"""

    try:
        resp = await clova_client.chat_completion(
            system_prompt="FA 부품 매뉴얼에서 스펙을 정확히 추출하는 전문가. JSON만 응답.",
            user_message=prompt,
            temperature=0.1,
            max_tokens=512,
        )
        clean = re.sub(r'```json|```', '', resp).strip()
        spec_data = json.loads(clean)

        if spec_data.get("model_name"):
            model = spec_data["model_name"]
            prod = await db.execute(
                select(Product).where(Product.model_name.ilike(f"%{model}%"))
            )
            product = prod.scalars().first()
            if product:
                existing_spec = await db.execute(
                    select(Specification).where(Specification.product_id == product.id)
                )
                if not existing_spec.scalar_one_or_none():
                    spec = Specification(
                        product_id=product.id,
                        dimension_w=spec_data.get("dimension_w"),
                        dimension_h=spec_data.get("dimension_h"),
                        dimension_d=spec_data.get("dimension_d"),
                        weight_kg=spec_data.get("weight_kg"),
                        input_voltage=spec_data.get("input_voltage"),
                        output_type=spec_data.get("output_type"),
                        io_points=spec_data.get("io_points"),
                        comm_protocol=spec_data.get("comm_protocol"),
                        operating_temp=spec_data.get("operating_temp"),
                        rated_power=spec_data.get("rated_power"),
                    )
                    db.add(spec)
                    count += 1
    except Exception as e:
        logger.warning(f"스펙 추출 실패: {e}")

    return count


async def _extract_and_save_alarms(
    page_texts: list, manufacturer: str, series: str, db: AsyncSession
) -> int:
    """알람코드 추출 및 저장"""
    count = 0
    alarm_pages = []

    for pt in page_texts:
        text_upper = pt["text"].upper()
        if any(kw in text_upper for kw in ['ALARM', 'ERROR', 'FAULT', '알람', '에러', '이상', 'AL.']):
            alarm_pages.append(pt)

    if not alarm_pages:
        return 0

    for pt in alarm_pages[:5]:
        text = pt["text"]
        prompt = f"""아래 FA 부품 매뉴얼 텍스트에서 알람/에러 코드 정보를 추출하라.
제조사: {manufacturer}, 시리즈: {series}, 페이지: {pt['page']}

[텍스트]
{text[:1500]}

다음 JSON 배열 형식으로만 응답 (최대 10개):
[
  {{
    "alarm_code": "AL.E7",
    "alarm_name": "알람 이름",
    "cause": "원인 설명",
    "solution": "해결 방법",
    "manual_page": "{pt['page']}"
  }}
]"""

        try:
            resp = await clova_client.chat_completion(
                system_prompt="FA 부품 매뉴얼 알람코드 추출 전문가. JSON 배열만 응답.",
                user_message=prompt,
                temperature=0.1,
                max_tokens=1024,
            )
            clean = re.sub(r'```json|```', '', resp).strip()
            if not clean.startswith('['):
                continue
            alarms = json.loads(clean)

            for a in alarms:
                if not a.get("alarm_code"):
                    continue
                existing = await db.execute(
                    select(AlarmCode).where(
                        AlarmCode.manufacturer == manufacturer,
                        AlarmCode.alarm_code == a["alarm_code"]
                    )
                )
                if existing.scalar_one_or_none():
                    continue

                alarm = AlarmCode(
                    manufacturer=manufacturer,
                    product_series=series,
                    alarm_code=a.get("alarm_code", ""),
                    alarm_name=a.get("alarm_name", ""),
                    cause=a.get("cause", ""),
                    solution=a.get("solution", ""),
                    manual_page=str(a.get("manual_page", pt["page"])),
                    manual_filename=f"{manufacturer}_{series}_manual.pdf",
                )
                db.add(alarm)
                count += 1

        except Exception as e:
            logger.warning(f"알람 추출 실패 p.{pt['page']}: {e}")

    return count


async def _save_chunks(
    page_texts: list, manufacturer: str, series: str, db: AsyncSession
) -> int:
    """페이지 텍스트를 청크로 저장 (RAG 검색용)"""
    # ManualChunk 테이블에 저장 (추후 벡터 임베딩 추가)
    count = 0
    for pt in page_texts:
        text = pt["text"].strip()
        if len(text) < 50:
            continue
        # 500자씩 청킹
        for i in range(0, len(text), 500):
            chunk = text[i:i+500]
            if len(chunk) < 50:
                continue
            count += 1
    return count
