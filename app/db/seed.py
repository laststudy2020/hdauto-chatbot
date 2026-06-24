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
    ("MR-J2S-10A",3,2),("MR-J2S-20A",2,2),("MR-J2S-40A",1,2),("MR-J2S-70A",2,2),
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
        # 알람코드는 별도 체크
        await seed_alarm_codes(db)
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
    await seed_alarm_codes(db)

# ─── 알람코드 데이터 ───
ALARM_CODES = [
    # MR-J4 서보드라이브 알람코드
    ("Mitsubishi","MELSERVO-J4","AL.10","과전압","주회로 전원전압이 허용값 초과","전원전압 확인, 회생저항 점검"),
    ("Mitsubishi","MELSERVO-J4","AL.11","저전압","주회로/제어회로 전원전압 부족","전원전압 확인, 전원배선 점검"),
    ("Mitsubishi","MELSERVO-J4","AL.12","메모리 이상","RAM/ROM 이상","서보앰프 교체"),
    ("Mitsubishi","MELSERVO-J4","AL.13","시계 이상","CPU 내부 클럭 이상","서보앰프 교체"),
    ("Mitsubishi","MELSERVO-J4","AL.15","메모리 이상2","EEPROM 이상","서보앰프 교체"),
    ("Mitsubishi","MELSERVO-J4","AL.16","인코더 초기통신 이상","인코더 초기통신 불량","인코더 케이블 점검, 인코더 교체"),
    ("Mitsubishi","MELSERVO-J4","AL.17","보드 이상","회로기판 이상","서보앰프 교체"),
    ("Mitsubishi","MELSERVO-J4","AL.19","메모리 이상3","플래시 ROM 이상","서보앰프 교체"),
    ("Mitsubishi","MELSERVO-J4","AL.1A","모터 조합 이상","서보앰프와 서보모터 조합 불일치","모터 파라미터 재설정"),
    ("Mitsubishi","MELSERVO-J4","AL.20","인코더 이상","인코더 신호 이상","인코더 케이블 점검, 노이즈 대책"),
    ("Mitsubishi","MELSERVO-J4","AL.24","주회로 이상","주회로 전원 위상 결상","전원배선 점검, 전원 3상 확인"),
    ("Mitsubishi","MELSERVO-J4","AL.25","절대위치 소실","절대위치 데이터 소실","배터리 교체 후 원점복귀"),
    ("Mitsubishi","MELSERVO-J4","AL.2A","모터 이상","모터 내부 이상","모터 교체"),
    ("Mitsubishi","MELSERVO-J4","AL.30","회생 이상","회생전력 과대","회생저항 용량 확인, 감속시간 연장"),
    ("Mitsubishi","MELSERVO-J4","AL.31","과속도","모터 속도가 순간 허용속도 초과","속도 제한값 확인, 가감속 시간 조정"),
    ("Mitsubishi","MELSERVO-J4","AL.32","과전류","순간 과전류 발생","출력배선 단락 확인, 모터 절연 확인"),
    ("Mitsubishi","MELSERVO-J4","AL.33","과전압2","주회로 과전압","회생저항 점검, 감속시간 연장"),
    ("Mitsubishi","MELSERVO-J4","AL.35","지령 주파수 이상","지령 펄스 주파수 이상","상위 컨트롤러 출력 확인"),
    ("Mitsubishi","MELSERVO-J4","AL.37","파라미터 이상","파라미터 설정값 이상","파라미터 초기화 후 재설정"),
    ("Mitsubishi","MELSERVO-J4","AL.45","주회로 소자 과열","IPM 과열","주변온도 확인, 냉각팬 점검"),
    ("Mitsubishi","MELSERVO-J4","AL.46","서보앰프 과열","서보앰프 내부 과열","주변온도 확인, 냉각팬 점검, 부하율 확인"),
    ("Mitsubishi","MELSERVO-J4","AL.47","냉각팬 이상","냉각팬 고장","냉각팬 교체"),
    ("Mitsubishi","MELSERVO-J4","AL.50","과부하1","연속 과부하","부하 경감, 가감속 시간 조정, 모터 용량 재검토"),
    ("Mitsubishi","MELSERVO-J4","AL.51","과부하2","순시 과부하","부하 경감, 기계계 점검"),
    ("Mitsubishi","MELSERVO-J4","AL.52","오차 과대","위치 편차량 초과","게인 조정, 가감속 시간 확인, 기계계 점검"),
    ("Mitsubishi","MELSERVO-J4","AL.8A","배터리 단선","배터리 케이블 단선","배터리 케이블 점검 및 교체"),
    ("Mitsubishi","MELSERVO-J4","AL.8E","배터리 전압 저하","배터리 전압 부족","배터리 교체"),
    ("Mitsubishi","MELSERVO-J4","AL.92","배터리 소모","배터리 수명 종료","배터리 교체"),
    ("Mitsubishi","MELSERVO-J4","AL.9F","옵션 카드 이상","옵션 카드 통신 이상","옵션 카드 점검 및 재장착"),
    ("Mitsubishi","MELSERVO-J4","AL.E0","과회생","회생전력 허용값 초과","회생저항 용량 재검토"),
    ("Mitsubishi","MELSERVO-J4","AL.E1","과부하3","연속운전 과부하","부하율 확인, 모터 용량 재검토"),
    ("Mitsubishi","MELSERVO-J4","AL.E3","절대위치 카운터 경고","절대위치 카운터 이상","원점복귀 실행"),
    ("Mitsubishi","MELSERVO-J4","AL.E6","서보 강제정지 경고","서보 강제정지 신호 입력","EM2 신호 확인"),
    ("Mitsubishi","MELSERVO-J4","AL.E7","컨트롤러 긴급정지 경고","상위 컨트롤러 긴급정지","상위 컨트롤러 상태 확인"),
    ("Mitsubishi","MELSERVO-J4","AL.E9","주회로 전원 OFF 경고","주회로 전원 차단 감지","주회로 전원 공급 확인"),
    # FR-E700 인버터 알람코드
    ("Mitsubishi","FR-E700","E.OC1","가속 중 과전류","가속 중 인버터 출력 전류 과대","가속시간 연장, 부하 점검, 모터 배선 확인"),
    ("Mitsubishi","FR-E700","E.OC2","정속 중 과전류","정속운전 중 과전류","부하 급변 확인, 모터 절연 측정"),
    ("Mitsubishi","FR-E700","E.OC3","감속 중 과전류","감속 중 과전류","감속시간 연장, 회생 브레이크 확인"),
    ("Mitsubishi","FR-E700","E.OV1","가속 중 과전압","가속 중 주회로 직류전압 과대","전원전압 확인"),
    ("Mitsubishi","FR-E700","E.OV2","정속 중 과전압","정속운전 중 과전압","전원전압 확인"),
    ("Mitsubishi","FR-E700","E.OV3","감속 중 과전압","감속 중 과전압","감속시간 연장, 제동저항 추가"),
    ("Mitsubishi","FR-E700","E.THT","인버터 과열","인버터 내부 과열","주변온도 확인, 냉각팬 점검, 부하율 확인"),
    ("Mitsubishi","FR-E700","E.THM","모터 과열","전자열동계전기 동작","부하 경감, Pr.9 설정 확인"),
    ("Mitsubishi","FR-E700","E.FIN","냉각팬 이상","냉각팬 고장","냉각팬 교체"),
    ("Mitsubishi","FR-E700","E.IPF","순간정전","순간정전 발생","전원 안정화"),
    ("Mitsubishi","FR-E700","E.UVT","저전압","주회로 전압 부족","전원전압 확인"),
    ("Mitsubishi","FR-E700","E.ILF","입력결상","3상 입력 전원 결상","입력 전원 3상 확인"),
    ("Mitsubishi","FR-E700","E.OLT","스톨방지","과부하로 인한 스톨방지 동작","부하 경감, Pr.22 설정 확인"),
    ("Mitsubishi","FR-E700","E.BE","제동트랜지스터 이상","제동저항 단락 또는 과전류","제동저항 및 배선 점검"),
    ("Mitsubishi","FR-E700","E.GF","지락","출력측 지락","모터 및 배선 절연 측정"),
    ("Mitsubishi","FR-E700","E.LF","출력결상","인버터 출력 결상","출력 배선 및 모터 확인"),
    ("Mitsubishi","FR-E700","E.OHT","외부 열동계전기","외부 OH 신호 입력","외부 과열 원인 제거"),
    ("Mitsubishi","FR-E700","E.PTC","PTC 서미스터","모터 PTC 서미스터 동작","모터 온도 확인"),
    ("Mitsubishi","FR-E700","E.MB1","브레이크 이상","브레이크 시퀀스 이상","브레이크 회로 점검"),
    ("Mitsubishi","FR-E700","E.MB2","브레이크 이상2","브레이크 개방 확인 이상","브레이크 피드백 신호 확인"),
    ("Mitsubishi","FR-E700","E.CPU","CPU 이상","CPU 연산 이상","인버터 전원 재투입, 이상 지속시 교체"),
    ("Mitsubishi","FR-E700","E.7","메모리 이상","EEPROM 이상","파라미터 초기화 후 재설정"),
    # FR-D700 인버터 알람코드
    ("Mitsubishi","FR-D700","E.OC1","가속 중 과전류","가속 중 인버터 출력 전류 과대","가속시간 연장, 부하 점검"),
    ("Mitsubishi","FR-D700","E.OC2","정속 중 과전류","정속운전 중 과전류","부하 확인, 모터 절연 측정"),
    ("Mitsubishi","FR-D700","E.OC3","감속 중 과전류","감속 중 과전류","감속시간 연장"),
    ("Mitsubishi","FR-D700","E.OV1","가속 중 과전압","가속 중 과전압","전원전압 확인"),
    ("Mitsubishi","FR-D700","E.OV2","정속 중 과전압","정속운전 중 과전압","전원전압 확인"),
    ("Mitsubishi","FR-D700","E.OV3","감속 중 과전압","감속 중 과전압","감속시간 연장, 제동저항 추가"),
    ("Mitsubishi","FR-D700","E.THT","인버터 과열","인버터 내부 과열","주변온도 확인, 냉각팬 점검"),
    ("Mitsubishi","FR-D700","E.THM","모터 과열","전자열동계전기 동작","부하 경감, Pr.9 확인"),
    ("Mitsubishi","FR-D700","E.IPF","순간정전","순간정전 발생","전원 안정화"),
    ("Mitsubishi","FR-D700","E.UVT","저전압","주회로 전압 부족","전원전압 확인"),
    ("Mitsubishi","FR-D700","E.GF","지락","출력측 지락","모터 및 배선 절연 측정"),
    ("Mitsubishi","FR-D700","E.LF","출력결상","인버터 출력 결상","출력 배선 확인"),
    ("Mitsubishi","FR-D700","E.OHT","외부 열동계전기","외부 OH 신호 입력","외부 과열 원인 제거"),
    # LS iG5A 인버터 알람코드 (매뉴얼 검증완료, 2026-06-24)
    ("LS","SV-iG5A","OCt","과전류","인버터 출력전류가 과전류 보호레벨 이상","가속시간 연장, 부하/배선 점검"),
    ("LS","SV-iG5A","OC2","과전류2","IGBT Arm 합선 또는 출력 합선 (11~22kW 해당)","배선 및 모터 절연 점검, 서비스센터 문의"),
    ("LS","SV-iG5A","GFt","지락전류","출력측 지락 발생으로 지락전류 흐름","출력배선 및 모터 절연 점검"),
    ("LS","SV-iG5A","IOL","인버터 과부하","출력전류가 정격전류 150% 1분 이상 연속","부하 경감, 인버터 용량 재검토"),
    ("LS","SV-iG5A","OLt","과부하트립","출력전류가 전동기 정격전류 설정값(F57) 초과","F57 설정 확인, 부하 점검"),
    ("LS","SV-iG5A","OHt","냉각핀 과열","주위온도 상승으로 냉각핀 과열","주변온도/통풍 확인, 냉각팬 점검"),
    ("LS","SV-iG5A","POt","출력결상","U,V,W 중 한 상 이상 결상","출력배선 및 모터 연결 확인"),
    ("LS","SV-iG5A","Out","과전압","DC전압이 규정치 초과(200V급 400Vdc/400V급 820Vdc)","감속시간 연장, 입력전압 확인"),
    ("LS","SV-iG5A","Lut","저전압","DC전압이 규정치 미달(200V급 180Vdc/400V급 360Vdc)","입력전원 및 배선 점검"),
    ("LS","SV-iG5A","EtH","전자써멀","전동기 과부하 운전으로 반한시 특성 동작","부하 경감, 전자써멀 설정값 확인"),
    ("LS","SV-iG5A","COL","입력결상","3상 입력 중 1상 결상 또는 평활콘덴서 수명","입력전원 3상 확인, 콘덴서 교체"),
    ("LS","SV-iG5A","FLtL","자기진단 고장","IGBT 파손, 출력단 합선/지락/개방","서비스센터 점검 필요"),
    ("LS","SV-iG5A","EEP","파라미터저장이상","파라미터 저장 시 이상, 전원투입시 표시","파라미터 초기화 후 재설정"),
    ("LS","SV-iG5A","Hvt","하드웨어이상","소프트웨어 이상","입력전원 차단 후 완전방전 뒤 재투입"),
    ("LS","SV-iG5A","Err","로더통신에러","인버터 제어부-로더 간 통신이상","입력전원 차단 후 완전방전 뒤 재투입"),
    ("LS","SV-iG5A","rErr","리모트통신에러","인버터-리모트로더 간 통신이상(운전유지)","통신케이블 점검"),
    ("LS","SV-iG5A","COm","로더이상","로더 이상 지속으로 본체가 로더 리셋","로더 연결 및 케이블 확인"),
    ("LS","SV-iG5A","FAn","냉각팬이상","냉각용 팬 고장","냉각팬 교체"),
    ("LS","SV-iG5A","ESt","출력순시차단","비상정지(EST) 단자 ON","EST 단자 OFF 시 재운전(FX/RX ON 상태일 때)"),
    ("LS","SV-iG5A","EtA","A접점고장신호","다기능입력단자(I17~I24) 18번 설정 단자 ON","외부 트립 신호 원인 확인"),
    ("LS","SV-iG5A","Etb","B접점고장신호","다기능입력단자(I17~I24) 19번 설정 단자 ON","외부 트립 신호 원인 확인"),
    ("LS","SV-iG5A","ntC","NTC오픈","NTC 써미스터 오픈","온도센서(NTC) 점검 및 교체"),
    ("LS","SV-iG5A","nbr","브레이크제어이상","브레이크제어시 전동기전류가 설정값(I82) 이하 10초 이상 유지","브레이크 회로 및 I82 설정 확인"),
]


async def seed_alarm_codes(db: AsyncSession):
    """알람코드 자동 복원"""
    from app.db.models import AlarmCode
    count = (await db.execute(select(func.count(AlarmCode.id)))).scalar()
    if count and count > 0:
        return

    logger.info("알람코드 데이터 복원 중...")
    for mfr, series, code, name, cause, solution in ALARM_CODES:
        db.add(AlarmCode(
            manufacturer=mfr,
            product_series=series,
            alarm_code=code,
            alarm_name=name,
            cause=cause,
            solution=solution,
            manual_page="",
            manual_filename=f"{mfr}_{series}_manual",
        ))
    await db.commit()
    logger.info(f"알람코드 {len(ALARM_CODES)}개 복원 완료")
