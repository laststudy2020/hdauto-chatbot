"""
1회성 마이그레이션 스크립트
- 기존에 잘못 등록된 LS SV-iG5A 알람코드(14개, 매뉴얼 미검증)를 삭제하고
- 공식 매뉴얼(IG5A 사용설명서 V2.0)에서 검증한 23개 코드로 교체합니다.
실행: python fix_ig5a_alarms.py
"""
import asyncio
from sqlalchemy import delete
from app.db.database import async_session
from app.db.models import AlarmCode

VERIFIED_IG5A_ALARMS = [
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


async def main():
    async with async_session() as db:
        result = await db.execute(
            delete(AlarmCode).where(
                AlarmCode.manufacturer == "LS",
                AlarmCode.product_series == "SV-iG5A",
            )
        )
        print(f"기존 LS 코드 {result.rowcount}개 삭제")

        for mfr, series, code, name, cause, solution in VERIFIED_IG5A_ALARMS:
            db.add(AlarmCode(
                manufacturer=mfr, product_series=series,
                alarm_code=code, alarm_name=name,
                cause=cause, solution=solution,
                manual_page="", manual_filename="iG5A_사용설명서_V2.0",
            ))
        await db.commit()
        print(f"검증된 iG5A 코드 {len(VERIFIED_IG5A_ALARMS)}개 등록 완료")


if __name__ == "__main__":
    asyncio.run(main())