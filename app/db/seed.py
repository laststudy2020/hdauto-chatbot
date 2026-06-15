"""
서버 시작 시 자동으로 기본 데이터를 복원하는 시드 스크립트
- Render 재시작 시 SQLite DB가 초기화되어도 자동 복원
"""
import csv
import io
import logging
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Product, Replacement, Specification, Inventory, ProductStatus

logger = logging.getLogger(__name__)

# ─── 샘플 제품 데이터 (하드코딩) ───
PRODUCTS = [
    ("FX3U-32MT/ES","MELSEC-FX3U","Mitsubishi","PLC","discontinued","2024-03",0),
    ("FX3U-16MT/ES","MELSEC-FX3U","Mitsubishi","PLC","discontinued","2024-03",0),
    ("FX3U-48MT/ES","MELSEC-FX3U","Mitsubishi","PLC","discontinued","2024-03",0),
    ("FX5U-32MT/ES","MELSEC-FX5U","Mitsubishi","PLC","active","",285000),
    ("FX5U-16MT/ES","MELSEC-FX5U","Mitsubishi","PLC","active","",195000),
    ("FX5U-48MT/ES","MELSEC-FX5U","Mitsubishi","PLC","active","",365000),
    ("MR-J2S-70A","MELSERVO-J2S","Mitsubishi","servo","discontinued","2023-06",0),
    ("MR-J4-70A","MELSERVO-J4","Mitsubishi","servo","active","",420000),
    ("MR-J2S-40A","MELSERVO-J2S","Mitsubishi","servo","discontinued","2023-06",0),
    ("MR-J4-40A","MELSERVO-J4","Mitsubishi","servo","active","",350000),
    ("MR-J2S-10A","MELSERVO-J2S","Mitsubishi","servo","discontinued","2023-06",0),
    ("MR-J4-10A","MELSERVO-J4","Mitsubishi","servo","active","",185000),
    ("MR-J2S-20A","MELSERVO-J2S","Mitsubishi","servo","discontinued","2023-06",0),
    ("MR-J4-20A","MELSERVO-J4","Mitsubishi","servo","active","",220000),
    ("XBM-DR16S","XGB","LS","PLC","active","",145000),
    ("XBM-DN32S","XGB","LS","PLC","active","",185000),
    ("XBC-DR30SU","XBC","LS","PLC","discontinued","2022-12",0),
    ("SV015iG5A-4","SV-iG5A","LS","inverter","active","",165000),
    ("SV008iG5A-4","SV-iG5A","LS","inverter","active","",135000),
    ("SV-iS7-015P5","SV-iS7","LS","inverter","active","",285000),
    ("FR-E740-0.75K","FR-E740","Mitsubishi","inverter","discontinued","2023-12",0),
    ("FR-E840-0080-4-60","FR-E840","Mitsubishi","inverter","active","",245000),
    ("E40H8-1024-3-T-24","E40H","Autonics","encoder","active","",85000),
    ("E50S8-1024-3-T-24","E50S","Autonics","encoder","active","",95000),
    ("BF5R-D1-N","BF5R","Autonics","sensor","active","",65000),
    ("PR12-4DN","PR","Autonics","sensor","active","",18000),
    ("GP-4301T","GP4000","Proface","HMI","discontinued","2023-09",0),
    ("SP5B41","SP5000","Proface","HMI","active","",850000),
]

REPLACEMENTS = [
    ("FX3U-32MT/ES","FX5U-32MT/ES","GX Works3 program conversion required. RS-422 -> Ethernet.",True,True,False),
    ("FX3U-16MT/ES","FX5U-16MT/ES","GX Works3 conversion required.",True,True,False),
    ("FX3U-48MT/ES","FX5U-48MT/ES","GX Works3 conversion required.",True,True,False),
    ("MR-J2S-70A","MR-J4-70A","Connector change required. Parameter reset needed.",False,False,False),
    ("MR-J2S-40A","MR-J4-40A","Connector change required. Parameter reset needed.",False,False,False),
    ("MR-J2S-10A","MR-J4-10A","Connector change required. Parameter reset needed.",False,False,False),
    ("MR-J2S-20A","MR-J4-20A","Connector change required. Parameter reset needed.",False,False,False),
    ("FR-E740-0.75K","FR-E840-0080-4-60","Parameter system changed. FR Configurator2 migration possible.",True,False,False),
    ("GP-4301T","SP5B41","Screen project conversion tool available.",True,False,False),
    ("XBC-DR30SU","XBM-DR16S","I/O point reduction. Program review required.",True,False,False),
]

SPECS = [
    ("FX5U-32MT/ES",150,90,83,0.87,"AC100-240V","Transistor","16in/16out","Ethernet/RS-485","0~55"),
    ("FX5U-16MT/ES",90,90,83,0.65,"AC100-240V","Transistor","8in/8out","Ethernet/RS-485","0~55"),
    ("FX5U-48MT/ES",182,90,83,1.1,"AC100-240V","Transistor","24in/24out","Ethernet/RS-485","0~55"),
    ("MR-J4-70A",55,170,167,1.6,"AC200-240V","","","RS-422/USB","0~55"),
    ("MR-J4-40A",55,170,167,1.5,"AC200-240V","","","RS-422/USB","0~55"),
    ("MR-J4-10A",40,150,167,1.1,"AC200-240V","","","RS-422/USB","0~55"),
    ("MR-J4-20A",40,150,167,1.2,"AC200-240V","","","RS-422/USB","0~55"),
    ("XBM-DR16S",100,110,90,0.45,"DC24V","Relay","8in/8out","RS-485","0~55"),
    ("XBM-DN32S",130,110,90,0.6,"DC24V","Transistor","16in/16out","RS-485","0~55"),
    ("SV015iG5A-4",68,128,130,1.0,"AC380-480V","","","RS-485","0~50"),
    ("SV008iG5A-4",68,128,130,0.85,"AC380-480V","","","RS-485","0~50"),
    ("FR-E840-0080-4-60",68,128,128,0.95,"AC380-480V","","","RS-485/USB","0~50"),
    ("E40H8-1024-3-T-24",40,None,None,0.25,"DC12-24V","","","",""),
    ("E50S8-1024-3-T-24",50,None,None,0.35,"DC12-24V","","","",""),
]

INVENTORY = [
    ("FX5U-32MT/ES",5,3),("FX5U-16MT/ES",8,3),("FX5U-48MT/ES",3,2),
    ("MR-J4-70A",2,2),("MR-J4-40A",4,2),("MR-J4-10A",3,2),("MR-J4-20A",5,2),
    ("XBM-DR16S",12,5),("XBM-DN32S",7,3),
    ("SV015iG5A-4",6,3),("SV008iG5A-4",9,3),
    ("FR-E840-0080-4-60",4,2),
    ("E40H8-1024-3-T-24",15,5),("E50S8-1024-3-T-24",10,5),
    ("BF5R-D1-N",20,5),("PR12-4DN",30,10),
]


async def seed_if_empty(db: AsyncSession):
    """DB가 비어있으면 기본 데이터 자동 삽입"""
    count = (await db.execute(select(func.count(Product.id)))).scalar()
    if count and count > 0:
        logger.info(f"DB에 제품 {count}개 이미 존재. 시드 스킵.")
        return

    logger.info("DB 비어있음 — 기본 데이터 자동 복원 시작...")

    # 1) 제품 등록
    product_map = {}
    for m, series, mfr, cat, status, disc_date, price in PRODUCTS:
        p = Product(
            model_name=m, series=series, manufacturer=mfr,
            category=cat, status=ProductStatus(status),
            discontinued_date=disc_date or None,
            our_price=price or None,
        )
        db.add(p)
        await db.flush()
        product_map[m] = p.id

    # 2) 대체품 매핑
    for old, new, notes, prog, term, dim in REPLACEMENTS:
        if old in product_map and new in product_map:
            db.add(Replacement(
                old_model_id=product_map[old],
                new_model_id=product_map[new],
                compatibility_notes=notes,
                program_convertible=prog,
                terminal_compatible=term,
                dimension_compatible=dim,
            ))

    # 3) 스펙 등록
    for model, w, h, d, kg, volt, out, io, comm, temp in SPECS:
        if model in product_map:
            db.add(Specification(
                product_id=product_map[model],
                dimension_w=w, dimension_h=h, dimension_d=d,
                weight_kg=kg, input_voltage=volt,
                output_type=out or None,
                io_points=io or None,
                comm_protocol=comm or None,
                operating_temp=temp or None,
            ))

    # 4) 재고 등록
    for model, stock, threshold in INVENTORY:
        if model in product_map:
            db.add(Inventory(
                product_id=product_map[model],
                current_stock=stock,
                min_threshold=threshold,
            ))

    await db.commit()
    logger.info(f"시드 완료: 제품 {len(PRODUCTS)}개, 대체품 {len(REPLACEMENTS)}개, 스펙 {len(SPECS)}개")
