"""
MR-J2S-A / MR-J2S-B 시리즈 서보드라이브 32종 등록 (J2S-A_08_.pdf, J2S-B_kor_.pdf 매뉴얼 직접 추출)

데이터 출처:
- 1.3절 "서보앰프 표준사양" -> 무게(weight_kg), 전압
- 1.5절 "형명의 구성" -> 용량코드 매핑 (10=100W ... 22K=22kW)
- 1.6절 "서보모터와의 조합" -> 호환 서보모터 (compatible_motors)
- 11장(A)/10장(B) "외형 치수도" -> 외형치수(W/H/D)

⚠ 매뉴얼 자체 오류 발견 및 수정:
A매뉴얼 1.3절 요약표에는 MR-J2S-700A 무게가 15kg으로 기재되어 있으나,
11.1절 개별 치수도에는 7.2kg으로 명시되어 있고 B매뉴얼의 700B(7.2kg)와도
일치함. 개별 치수도 값(7.2kg)을 신뢰하여 등록함 (요약표 자체의 인쇄 오류로 판단).

⚠ 모터 자체의 외형치수(W/H/D)는 이 매뉴얼에 없음 — 별도 "서보모터 기술자료집"
필요. 호환모터는 모델명 리스트만 등록 (기존 패턴과 동일, extra_specs.compatible_motors).
11KA/15KA/22KA 그룹은 도면에서 깊이(D) 치수가 명확히 표기되지 않아 비워둠.

실행: python register_mrj2s_manual.py
"""
import asyncio
from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Product, Specification, ProductStatus

# 용량코드 -> 용량(W) (1.5절 형명의 구성)
CAPACITY_W = {
    "10": 100, "20": 200, "40": 400, "60": 600, "70": 750, "100": 1000,
    "200": 2000, "350": 3500, "500": 5000, "700": 7000,
    "11K": 11000, "15K": 15000, "22K": 22000,
}

# 모델접미(코드, 무게kg, W, H, D[None=미확인]) — A/B 공통 (1.3절 + 11장/10장 치수도)
MODEL_SPECS = [
    ("10", 0.7, 50, 168, 135),
    ("20", 0.7, 50, 168, 135),
    ("40", 1.1, 70, 168, 135),
    ("60", 1.1, 70, 168, 135),
    ("70", 1.7, 70, 168, 190),
    ("100", 1.7, 70, 168, 190),
    ("200", 2.0, 90, 168, 195),
    ("350", 2.0, 90, 168, 195),
    ("500", 4.9, 130, 250, 200),
    ("700", 7.2, 180, 350, 200),   # 매뉴얼 요약표 오류(15kg) -> 치수도 값(7.2kg)으로 수정
    ("11K", 15, 236, 376, None),
    ("15K", 16, 236, 376, None),
    ("22K", 20, 326, 376, None),
]
# 단상 AC100-120V 변형 (동일 용량·치수, 전원만 다름)
SINGLE_PHASE_VARIANTS = ["10", "20", "40"]

# 코드 -> 호환 서보모터 목록 (1.6절, A/B 공통 — 모터 모델명 자체는 접미사 없음)
COMPATIBLE_MOTORS = {
    "10": ["HC-KFS053", "HC-KFS13", "HC-MFS053", "HC-MFS13", "HC-UFS13"],
    "20": ["HC-KFS23", "HC-MFS23", "HC-UFS23"],
    "40": ["HC-KFS43", "HC-MFS43", "HC-UFS43"],
    "60": ["HC-SFS52", "HC-SFS53", "HC-LFS52"],
    "70": ["HC-KFS73", "HC-MFS73", "HC-UFS72", "HC-UFS73"],
    "100": ["HC-SFS81", "HC-SFS102", "HC-SFS103", "HC-LFS102"],
    "200": ["HC-SFS121", "HC-SFS201", "HC-SFS152", "HC-SFS202", "HC-SFS153",
            "HC-SFS203", "HC-RFS103", "HC-RFS153", "HC-UFS152", "HC-LFS152"],
    "350": ["HC-SFS301", "HC-SFS352", "HC-SFS353", "HC-RFS203", "HC-UFS202", "HC-LFS202"],
    "500": ["HC-SFS502", "HC-RFS353", "HC-RFS503", "HC-UFS352", "HC-UFS502",
            "HC-LFS302", "HA-LFS502"],
    "700": ["HC-SFS702", "HC-LFS601", "HC-LFS701M", "HC-LFS702"],
    "11K": ["HC-LFS801", "HC-LFS12K1", "HC-LFS11K1M", "HC-LFS11K2"],
    "15K": ["HC-LFS15K1", "HC-LFS15K1M", "HC-LFS15K2"],
    "22K": ["HC-LFS20K1", "HC-LFS25K1", "HC-LFS22K1M", "HC-LFS22K2"],
}

BRAKE_NOTE = "전자 브레이크 부착 모터도 동일한 조합으로 사용 가능합니다 (매뉴얼 1.6절)."


async def _upsert(db, model_name, series, voltage, capacity_w, weight_kg, w, h, d, motors, interface_note):
    extra_specs = {
        "capacity_w": capacity_w,
        "compatible_motors": motors,
        "interface_note": interface_note,
        "brake_note": BRAKE_NOTE,
    }
    result = await db.execute(select(Product).where(Product.model_name == model_name))
    product = result.scalar_one_or_none()

    if product:
        spec_result = await db.execute(select(Specification).where(Specification.product_id == product.id))
        spec = spec_result.scalar_one_or_none()
        if spec:
            spec.weight_kg = weight_kg
            spec.dimension_w, spec.dimension_h, spec.dimension_d = w, h, d
            spec.input_voltage = voltage
            spec.extra_specs = extra_specs
        else:
            db.add(Specification(
                product_id=product.id, weight_kg=weight_kg,
                dimension_w=w, dimension_h=h, dimension_d=d,
                input_voltage=voltage, extra_specs=extra_specs,
            ))
        print(f"보완: {model_name}")
    else:
        new_product = Product(
            model_name=model_name, series=series, manufacturer="Mitsubishi",
            category="servo", status=ProductStatus.DISCONTINUED,
            discontinued_date="2023-06",
        )
        db.add(new_product)
        await db.flush()
        db.add(Specification(
            product_id=new_product.id, weight_kg=weight_kg,
            dimension_w=w, dimension_h=h, dimension_d=d,
            input_voltage=voltage, extra_specs=extra_specs,
        ))
        print(f"신규 등록: {model_name}")


async def main():
    async with async_session() as db:
        for suffix, label, interface_note in [
            ("A", "A", "범용 인터페이스 (RS-422/RS-232C)"),
            ("B", "B", "SSCNET 대응 (서보 시스템 콘트롤러 네트워크)"),
        ]:
            print(f"[MR-J2S-{label} 시리즈]")
            for code, weight_kg, w, h, d in MODEL_SPECS:
                model_name = f"MR-J2S-{code}{suffix}"
                voltage = "삼상AC200-230V/단상AC230V" if code not in ("500", "700", "11K", "15K", "22K") else "삼상AC200-230V"
                await _upsert(
                    db, model_name, f"MELSERVO-J2S-{label}", voltage,
                    CAPACITY_W[code], weight_kg, w, h, d,
                    COMPATIBLE_MOTORS[code], interface_note,
                )
            # 단상 AC100-120V 변형 (10/20/40 용량만 존재)
            for code in SINGLE_PHASE_VARIANTS:
                weight_kg, w, h, d = next((wt, ww, hh, dd) for c, wt, ww, hh, dd in MODEL_SPECS if c == code)
                model_name = f"MR-J2S-{code}{suffix}1"
                await _upsert(
                    db, model_name, f"MELSERVO-J2S-{label}", "단상AC100-120V",
                    CAPACITY_W[code], weight_kg, w, h, d,
                    COMPATIBLE_MOTORS[code], interface_note,
                )

        await db.commit()
        print("\n완료 — MR-J2S-A/B 시리즈 32개 모델 처리")


if __name__ == "__main__":
    asyncio.run(main())