"""
APEX AB/ABR 시리즈 planetary gearbox(감속기) 카탈로그 등록.

데이터 출처: docs/datasheets/apex_reduer/apex감속기 06AB+Series.pdf
- p.71: AB 시리즈 Gearbox Performance (정격토크/입력회전수/백래시/무게 등)
- p.72: AB 1단 감속(i=3~10) Dimension (입력홀 규격 C3 포함)
- p.73: AB 2단 감속(i=15~100) Dimension
- p.74: ABR 시리즈 Gearbox Performance
- p.75: ABR 1단 감속(i=3~20) Dimension
- p.76: ABR 2단 감속(i=25~200) Dimension

표 추출이 줄바꿈으로 깨졌던 셀(AB060A/AB090A 2단 입력홀, ABR 2단 입력홀)은
페이지를 300dpi 이미지로 렌더링해 육안으로 재확인한 값 — 확정값이며 추측 아님
(docs/superpowers/specs/2026-07-11-apex-reducer-matching-design.md의
"데이터 확정값" 표와 동일).

이번 등록에서 제외한 것:
- C1~C10 (모터 장착 어댑터 치수): 카탈로그 각주 "C1~C10은 적용모터에 따라
  다릅니다. 당사 홈페이지... Design Tool을 이용하여 확인"에 따라 이 카탈로그
  값만으로는 확정 불가 — 컬럼 자체를 두지 않는다. 매칭 로직(servo_spec_search.py의
  find_reducer_compat)도 이 값을 쓰지 않고, 매칭 결과에는 항상
  "정확한 모터 장착 어댑터(C1~C10)는 APEX 측 확인이 필요합니다" 안내를 자동 첨부한다.
- P0=★ (AB090 2단): 표준 미생산이지만 "고객 요청 시 최대한 정밀하게 제작 가능
  (납기/가격 변동)" — backlash_p0_arcmin=None, backlash_note로 별도 보존.

실행: python register_apex_reducer.py
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.db.database import async_session
from app.db.models import Reducer

_AB1_RATIOS = [3, 4, 5, 6, 7, 8, 9, 10]
_AB2_RATIOS = [15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100]
_ABR1_RATIOS = [3, 4, 5, 6, 7, 8, 9, 10, 14, 20]
_ABR2_RATIOS = [25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100, 120, 140, 160, 180, 200]

_AB_SOURCE = "apex감속기 06AB+Series.pdf p.71(spec)/{dim_page}(dim)"
_ABR_SOURCE = "apex감속기 06AB+Series.pdf p.74(spec)/{dim_page}(dim)"

REDUCER_ROWS = [
    # ── AB 1단 (i=3~10), p.72 ──
    dict(series="AB", model_name="AB042", stage=1, ratio_list=_AB1_RATIOS, ratio_range_label="3~10",
         input_bore_std_mm=11, input_bore_optional_mm=12,
         rated_torque_min_nm=14, rated_torque_max_nm=22,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=0.6,
         backlash_p0_arcmin=None, backlash_p1_arcmin=3, backlash_p2_arcmin=5,
         backlash_note="P0급 제작안됨", source_note=_AB_SOURCE.format(dim_page="p.72")),
    dict(series="AB", model_name="AB060", stage=1, ratio_list=_AB1_RATIOS, ratio_range_label="3~10",
         input_bore_std_mm=14, input_bore_optional_mm=16,
         rated_torque_min_nm=40, rated_torque_max_nm=60,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=1.3,
         backlash_p0_arcmin=None, backlash_p1_arcmin=3, backlash_p2_arcmin=5,
         backlash_note="P0급 제작안됨", source_note=_AB_SOURCE.format(dim_page="p.72")),
    dict(series="AB", model_name="AB090", stage=1, ratio_list=_AB1_RATIOS, ratio_range_label="3~10",
         input_bore_std_mm=19, input_bore_optional_mm=24,
         rated_torque_min_nm=100, rated_torque_max_nm=160,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=3.7,
         backlash_p0_arcmin=1, backlash_p1_arcmin=3, backlash_p2_arcmin=5,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.72")),
    dict(series="AB", model_name="AB115", stage=1, ratio_list=_AB1_RATIOS, ratio_range_label="3~10",
         input_bore_std_mm=32, input_bore_optional_mm=None,
         rated_torque_min_nm=230, rated_torque_max_nm=330,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=7.8,
         backlash_p0_arcmin=1, backlash_p1_arcmin=3, backlash_p2_arcmin=5,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.72")),
    dict(series="AB", model_name="AB142", stage=1, ratio_list=_AB1_RATIOS, ratio_range_label="3~10",
         input_bore_std_mm=38, input_bore_optional_mm=None,
         rated_torque_min_nm=342, rated_torque_max_nm=650,
         rated_input_speed_rpm=3000, max_input_speed_rpm=6000, weight_kg=14.5,
         backlash_p0_arcmin=1, backlash_p1_arcmin=3, backlash_p2_arcmin=5,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.72")),
    dict(series="AB", model_name="AB180", stage=1, ratio_list=_AB1_RATIOS, ratio_range_label="3~10",
         input_bore_std_mm=48, input_bore_optional_mm=None,
         rated_torque_min_nm=588, rated_torque_max_nm=1200,
         rated_input_speed_rpm=3000, max_input_speed_rpm=6000, weight_kg=29,
         backlash_p0_arcmin=1, backlash_p1_arcmin=3, backlash_p2_arcmin=5,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.72")),
    dict(series="AB", model_name="AB220", stage=1, ratio_list=_AB1_RATIOS, ratio_range_label="3~10",
         input_bore_std_mm=55, input_bore_optional_mm=None,
         rated_torque_min_nm=1140, rated_torque_max_nm=2000,
         rated_input_speed_rpm=2000, max_input_speed_rpm=4000, weight_kg=48,
         backlash_p0_arcmin=1, backlash_p1_arcmin=3, backlash_p2_arcmin=5,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.72")),

    # ── AB 2단 (i=15~100), p.73 ──
    dict(series="AB", model_name="AB042", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=11, input_bore_optional_mm=12,
         rated_torque_min_nm=14, rated_torque_max_nm=22,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=0.8,
         backlash_p0_arcmin=None, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note="P0급 제작안됨", source_note=_AB_SOURCE.format(dim_page="p.73")),
    dict(series="AB", model_name="AB060", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=11, input_bore_optional_mm=12,
         rated_torque_min_nm=40, rated_torque_max_nm=60,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=1.5,
         backlash_p0_arcmin=None, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note="P0급 제작안됨", source_note=_AB_SOURCE.format(dim_page="p.73")),
    dict(series="AB", model_name="AB060A", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=14, input_bore_optional_mm=16,
         rated_torque_min_nm=40, rated_torque_max_nm=60,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=1.9,
         backlash_p0_arcmin=None, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note="P0급 제작안됨 (Special type)", source_note=_AB_SOURCE.format(dim_page="p.73")),
    dict(series="AB", model_name="AB090", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=14, input_bore_optional_mm=16,
         rated_torque_min_nm=100, rated_torque_max_nm=160,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=4.1,
         backlash_p0_arcmin=None, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note="P0급: 고객 요청 시 특별 제작 가능(★, 납기/가격 변동)",
         source_note=_AB_SOURCE.format(dim_page="p.73")),
    dict(series="AB", model_name="AB090A", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=19, input_bore_optional_mm=24,
         rated_torque_min_nm=100, rated_torque_max_nm=160,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=5.3,
         backlash_p0_arcmin=None, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note="P0급 제작안됨 (Special type)", source_note=_AB_SOURCE.format(dim_page="p.73")),
    dict(series="AB", model_name="AB115", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=19, input_bore_optional_mm=24,
         rated_torque_min_nm=230, rated_torque_max_nm=330,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=9,
         backlash_p0_arcmin=3, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.73")),
    dict(series="AB", model_name="AB142", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=32, input_bore_optional_mm=None,
         rated_torque_min_nm=342, rated_torque_max_nm=650,
         rated_input_speed_rpm=3000, max_input_speed_rpm=6000, weight_kg=17.5,
         backlash_p0_arcmin=3, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.73")),
    dict(series="AB", model_name="AB180", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=38, input_bore_optional_mm=None,
         rated_torque_min_nm=588, rated_torque_max_nm=1200,
         rated_input_speed_rpm=3000, max_input_speed_rpm=6000, weight_kg=33,
         backlash_p0_arcmin=3, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.73")),
    dict(series="AB", model_name="AB220", stage=2, ratio_list=_AB2_RATIOS, ratio_range_label="15~100",
         input_bore_std_mm=48, input_bore_optional_mm=None,
         rated_torque_min_nm=1140, rated_torque_max_nm=2000,
         rated_input_speed_rpm=2000, max_input_speed_rpm=4000, weight_kg=60,
         backlash_p0_arcmin=3, backlash_p1_arcmin=5, backlash_p2_arcmin=7,
         backlash_note=None, source_note=_AB_SOURCE.format(dim_page="p.73")),

    # ── ABR 1단 (i=3~20), p.75 ──
    dict(series="ABR", model_name="ABR042", stage=1, ratio_list=_ABR1_RATIOS, ratio_range_label="3~20",
         input_bore_std_mm=11, input_bore_optional_mm=12,
         rated_torque_min_nm=9, rated_torque_max_nm=19,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=0.9,
         backlash_p0_arcmin=None, backlash_p1_arcmin=4, backlash_p2_arcmin=6,
         backlash_note="P0급 제작안됨", source_note=_ABR_SOURCE.format(dim_page="p.75")),
    dict(series="ABR", model_name="ABR060", stage=1, ratio_list=_ABR1_RATIOS, ratio_range_label="3~20",
         input_bore_std_mm=14, input_bore_optional_mm=16,
         rated_torque_min_nm=36, rated_torque_max_nm=60,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=2.1,
         backlash_p0_arcmin=None, backlash_p1_arcmin=4, backlash_p2_arcmin=6,
         backlash_note="P0급 제작안됨", source_note=_ABR_SOURCE.format(dim_page="p.75")),
    dict(series="ABR", model_name="ABR090", stage=1, ratio_list=_ABR1_RATIOS, ratio_range_label="3~20",
         input_bore_std_mm=19, input_bore_optional_mm=24,
         rated_torque_min_nm=90, rated_torque_max_nm=150,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=6.4,
         backlash_p0_arcmin=2, backlash_p1_arcmin=4, backlash_p2_arcmin=6,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.75")),
    dict(series="ABR", model_name="ABR115", stage=1, ratio_list=_ABR1_RATIOS, ratio_range_label="3~20",
         input_bore_std_mm=32, input_bore_optional_mm=None,
         rated_torque_min_nm=195, rated_torque_max_nm=325,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=13,
         backlash_p0_arcmin=2, backlash_p1_arcmin=4, backlash_p2_arcmin=6,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.75")),
    dict(series="ABR", model_name="ABR142", stage=1, ratio_list=_ABR1_RATIOS, ratio_range_label="3~20",
         input_bore_std_mm=38, input_bore_optional_mm=None,
         rated_torque_min_nm=342, rated_torque_max_nm=650,
         rated_input_speed_rpm=3000, max_input_speed_rpm=6000, weight_kg=24.5,
         backlash_p0_arcmin=2, backlash_p1_arcmin=4, backlash_p2_arcmin=6,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.75")),
    dict(series="ABR", model_name="ABR180", stage=1, ratio_list=_ABR1_RATIOS, ratio_range_label="3~20",
         input_bore_std_mm=48, input_bore_optional_mm=None,
         rated_torque_min_nm=588, rated_torque_max_nm=1200,
         rated_input_speed_rpm=3000, max_input_speed_rpm=6000, weight_kg=51,
         backlash_p0_arcmin=2, backlash_p1_arcmin=4, backlash_p2_arcmin=6,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.75")),
    dict(series="ABR", model_name="ABR220", stage=1, ratio_list=_ABR1_RATIOS, ratio_range_label="3~20",
         input_bore_std_mm=55, input_bore_optional_mm=None,
         rated_torque_min_nm=1140, rated_torque_max_nm=2000,
         rated_input_speed_rpm=2000, max_input_speed_rpm=4000, weight_kg=83,
         backlash_p0_arcmin=2, backlash_p1_arcmin=4, backlash_p2_arcmin=6,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.75")),

    # ── ABR 2단 (i=25~200), p.76 ──
    dict(series="ABR", model_name="ABR042", stage=2, ratio_list=_ABR2_RATIOS, ratio_range_label="25~200",
         input_bore_std_mm=11, input_bore_optional_mm=12,
         rated_torque_min_nm=14, rated_torque_max_nm=20,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=1.2,
         backlash_p0_arcmin=None, backlash_p1_arcmin=7, backlash_p2_arcmin=9,
         backlash_note="P0급 제작안됨", source_note=_ABR_SOURCE.format(dim_page="p.76")),
    dict(series="ABR", model_name="ABR060", stage=2, ratio_list=_ABR2_RATIOS, ratio_range_label="25~200",
         input_bore_std_mm=11, input_bore_optional_mm=12,
         rated_torque_min_nm=40, rated_torque_max_nm=60,
         rated_input_speed_rpm=5000, max_input_speed_rpm=10000, weight_kg=1.5,
         backlash_p0_arcmin=None, backlash_p1_arcmin=7, backlash_p2_arcmin=9,
         backlash_note="P0급 제작안됨", source_note=_ABR_SOURCE.format(dim_page="p.76")),
    dict(series="ABR", model_name="ABR090", stage=2, ratio_list=_ABR2_RATIOS, ratio_range_label="25~200",
         input_bore_std_mm=14, input_bore_optional_mm=16,
         rated_torque_min_nm=100, rated_torque_max_nm=150,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=7.8,
         backlash_p0_arcmin=4, backlash_p1_arcmin=7, backlash_p2_arcmin=9,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.76")),
    dict(series="ABR", model_name="ABR115", stage=2, ratio_list=_ABR2_RATIOS, ratio_range_label="25~200",
         input_bore_std_mm=19, input_bore_optional_mm=24,
         rated_torque_min_nm=230, rated_torque_max_nm=325,
         rated_input_speed_rpm=4000, max_input_speed_rpm=8000, weight_kg=14.2,
         backlash_p0_arcmin=4, backlash_p1_arcmin=7, backlash_p2_arcmin=9,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.76")),
    dict(series="ABR", model_name="ABR142", stage=2, ratio_list=_ABR2_RATIOS, ratio_range_label="25~200",
         input_bore_std_mm=32, input_bore_optional_mm=None,
         rated_torque_min_nm=450, rated_torque_max_nm=650,
         rated_input_speed_rpm=3000, max_input_speed_rpm=6000, weight_kg=27.5,
         backlash_p0_arcmin=4, backlash_p1_arcmin=7, backlash_p2_arcmin=9,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.76")),
    dict(series="ABR", model_name="ABR180", stage=2, ratio_list=_ABR2_RATIOS, ratio_range_label="25~200",
         input_bore_std_mm=38, input_bore_optional_mm=None,
         rated_torque_min_nm=900, rated_torque_max_nm=1200,
         rated_input_speed_rpm=3000, max_input_speed_rpm=6000, weight_kg=54,
         backlash_p0_arcmin=4, backlash_p1_arcmin=7, backlash_p2_arcmin=9,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.76")),
    dict(series="ABR", model_name="ABR220", stage=2, ratio_list=_ABR2_RATIOS, ratio_range_label="25~200",
         input_bore_std_mm=48, input_bore_optional_mm=None,
         rated_torque_min_nm=1500, rated_torque_max_nm=2000,
         rated_input_speed_rpm=2000, max_input_speed_rpm=4000, weight_kg=95,
         backlash_p0_arcmin=4, backlash_p1_arcmin=7, backlash_p2_arcmin=9,
         backlash_note=None, source_note=_ABR_SOURCE.format(dim_page="p.76")),
]


async def main():
    assert len(REDUCER_ROWS) == 30, f"행 개수가 30이 아님: {len(REDUCER_ROWS)}"

    async with async_session() as db:
        for row in REDUCER_ROWS:
            db.add(Reducer(**row))
        await db.commit()

    print(f"완료 - Reducer {len(REDUCER_ROWS)}행 등록 (AB 16행 + ABR 14행)")


if __name__ == "__main__":
    asyncio.run(main())
