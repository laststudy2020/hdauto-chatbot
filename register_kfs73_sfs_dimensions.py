"""미쓰비시 HC-KFS73/MFS73 + HC-SFS 시리즈 외형치수 등록 (축경/축길이 제외).

데이터 출처: docs/datasheets/mitsubishi_j2s/미쓰비시J2S시리즈서보모터.pdf
- 81페이지: HC-KFS73(B)/HC-MFS73(B) 외형치수
- 89페이지: HC-SFS81(B)~SFS153(B), SFS121(B)~SFS353(B) 외형치수

이번 배치에서 축경(shaft_diameter_mm)/축길이(shaft_length_mm)는 의도적으로 제외.
- shaft_diameter_mm을 넣으면 app/services/servo_spec_search.py의
  _match_reducers_by_bore()가 APEX AB/ABR 카탈로그와 자동매칭을 시도해
  아직 검증 안 된 감속기 호환표가 노출됨 (사용자 결정: 이번엔 보류).
- SFS 축길이는 도면상 후보값이 두 개(55 vs 50, 79 vs 75)라 판독 불확실 (사용자 결정: 보류).
프레임/몸체/전장/플랜지 볼트 규격만 등록.

400V "(4)" 접미 모델(SFS524/1024/1524/2024/3524/5024/7024)은 대응하는 드라이브
Product 행이 DB에 없어 제외. HC-KFS46/410도 동일한 이유로 제외
(register_hc_kfs_servo.py 주석 참조).

모델 -> 드라이브 코드 매핑은 register_mrj2s_manual.py가 이미 등록해둔 각 드라이브의
extra_specs.compatible_motors 목록을 기준으로 대조함. 물리적으로 같은 프레임이어도
드라이브 용량이 다르면 다른 code로 묶임 (예: SFS152/153은 □130 프레임이지만 code
200 드라이브에 연결 — SFS81과 프레임은 같지만 드라이브는 다름).

실행: python register_kfs73_sfs_dimensions.py --dry-run   (변경 여부만 출력, DB 미반영)
      python register_kfs73_sfs_dimensions.py              (실제 반영)
"""
import argparse
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import select
from app.db.database import async_session
from app.db.models import Product, Specification

FRAME_KFS80 = {
    "frame_size_mm": 80, "body_size_mm": 82,
    "flange_spec": "4-ø6.6 (볼트원 ø90)",
}
FRAME_SFS130 = {
    "frame_size_mm": 130, "body_size_mm": 130,
    "flange_spec": "4-ø9 (볼트원 ø165)",
}
FRAME_SFS176 = {
    "frame_size_mm": 176, "body_size_mm": 176,
    "flange_spec": "4-ø13.5 (볼트원 ø230)",
}

MOTORS = {
    "HC-KFS73": {"code": "70", "dimensions": {**FRAME_KFS80, "overall_length_mm": 142, "overall_length_mm_brake": 177.5}},
    "HC-MFS73": {"code": "70", "dimensions": {**FRAME_KFS80, "overall_length_mm": 142, "overall_length_mm_brake": 177.5}},

    "HC-SFS52": {"code": "60", "dimensions": {**FRAME_SFS130, "overall_length_mm": 120, "overall_length_mm_brake": 153}},
    "HC-SFS53": {"code": "60", "dimensions": {**FRAME_SFS130, "overall_length_mm": 120, "overall_length_mm_brake": 153}},

    "HC-SFS81": {"code": "100", "dimensions": {**FRAME_SFS130, "overall_length_mm": 170, "overall_length_mm_brake": 203}},
    "HC-SFS102": {"code": "100", "dimensions": {**FRAME_SFS130, "overall_length_mm": 145, "overall_length_mm_brake": 178}},
    "HC-SFS103": {"code": "100", "dimensions": {**FRAME_SFS130, "overall_length_mm": 145, "overall_length_mm_brake": 178}},

    "HC-SFS121": {"code": "200", "dimensions": {**FRAME_SFS176, "overall_length_mm": 145, "overall_length_mm_brake": 193}},
    "HC-SFS202": {"code": "200", "dimensions": {**FRAME_SFS176, "overall_length_mm": 145, "overall_length_mm_brake": 193}},
    "HC-SFS203": {"code": "200", "dimensions": {**FRAME_SFS176, "overall_length_mm": 145, "overall_length_mm_brake": 193}},
    "HC-SFS201": {"code": "200", "dimensions": {**FRAME_SFS176, "overall_length_mm": 187, "overall_length_mm_brake": 235}},
    "HC-SFS152": {"code": "200", "dimensions": {**FRAME_SFS130, "overall_length_mm": 170, "overall_length_mm_brake": 203}},
    "HC-SFS153": {"code": "200", "dimensions": {**FRAME_SFS130, "overall_length_mm": 170, "overall_length_mm_brake": 203}},

    "HC-SFS301": {"code": "350", "dimensions": {**FRAME_SFS176, "overall_length_mm": 208, "overall_length_mm_brake": 256}},
    "HC-SFS352": {"code": "350", "dimensions": {**FRAME_SFS176, "overall_length_mm": 187, "overall_length_mm_brake": 235}},
    "HC-SFS353": {"code": "350", "dimensions": {**FRAME_SFS176, "overall_length_mm": 187, "overall_length_mm_brake": 235}},

    "HC-SFS502": {"code": "500", "dimensions": {**FRAME_SFS176, "overall_length_mm": 208, "overall_length_mm_brake": 256}},

    "HC-SFS702": {"code": "700", "dimensions": {**FRAME_SFS176, "overall_length_mm": 292, "overall_length_mm_brake": 340}},
}

DRIVES_BY_CODE = {
    "70": ["MR-J2S-70A", "MR-J2S-70B"],
    "60": ["MR-J2S-60A", "MR-J2S-60B"],
    "100": ["MR-J2S-100A", "MR-J2S-100B"],
    "200": ["MR-J2S-200A", "MR-J2S-200B"],
    "350": ["MR-J2S-350A", "MR-J2S-350B"],
    "500": ["MR-J2S-500A", "MR-J2S-500B"],
    "700": ["MR-J2S-700A", "MR-J2S-700B"],
}

ENTRIES_BY_CODE: dict[str, dict] = {}
for motor_name, data in MOTORS.items():
    code = data["code"]
    entry = {"dimensions": data["dimensions"], "reducers": []}
    ENTRIES_BY_CODE.setdefault(code, {})[motor_name] = entry


async def _merge_motor_specs(db, model_name: str, code: str, dry_run: bool) -> str:
    result = await db.execute(select(Product).where(Product.model_name == model_name))
    product = result.scalar_one_or_none()
    if not product:
        return f"스킵 (드라이브 행 없음): {model_name}"

    spec_result = await db.execute(select(Specification).where(Specification.product_id == product.id))
    spec = spec_result.scalar_one_or_none()
    if not spec:
        return f"스킵 (Specification 없음): {model_name}"

    extra_specs = dict(spec.extra_specs or {})
    motor_specs = dict(extra_specs.get("motor_specs") or {})
    before_keys = set(motor_specs.keys())
    new_entries = ENTRIES_BY_CODE[code]

    added = [k for k in new_entries if k not in before_keys]
    overwritten = [k for k in new_entries if k in before_keys and motor_specs[k] != new_entries[k]]
    unchanged = [k for k in new_entries if k in before_keys and motor_specs[k] == new_entries[k]]

    if dry_run:
        msg = f"[dry-run] {model_name}: 추가={added or '-'} 덮어씀={overwritten or '-'} 동일={unchanged or '-'}"
        return msg

    motor_specs.update(new_entries)
    extra_specs["motor_specs"] = motor_specs
    spec.extra_specs = extra_specs
    return f"반영: {model_name} <- 추가={added or '-'} 덮어씀={overwritten or '-'}"


async def main(dry_run: bool):
    async with async_session() as db:
        for code, drive_names in DRIVES_BY_CODE.items():
            for model_name in drive_names:
                msg = await _merge_motor_specs(db, model_name, code, dry_run)
                print(msg)

        if dry_run:
            await db.rollback()
            print("\n[dry-run] DB에 반영하지 않음 (rollback)")
        else:
            await db.commit()
            print("\n완료 — HC-KFS73/MFS73 + HC-SFS 시리즈 외형치수(축경/축길이 제외) 등록")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
