"""
매뉴얼 PDF 로컬 업로드 스크립트
사용법: python upload_manual.py [PDF파일] [제조사] [시리즈]
예시: python upload_manual.py "MR-J4매뉴얼.pdf" Mitsubishi MELSERVO-J4

Supabase DB에 직접 저장하려면 .env의 DATABASE_URL을 Supabase URL로 변경
로컬 SQLite에 저장하려면 .env의 DATABASE_URL을 sqlite로 유지
"""
import asyncio
import sys
import os

# 현재 디렉토리를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def main():
    if len(sys.argv) < 4:
        print("사용법: python upload_manual.py [PDF파일경로] [제조사] [시리즈]")
        print("예시: python upload_manual.py manuals/MR-J4.pdf Mitsubishi MELSERVO-J4")
        print()
        print("지원 시리즈 예시:")
        print("  Mitsubishi / MELSERVO-J4   (MR-J4 서보드라이브)")
        print("  Mitsubishi / MELSERVO-J2S  (MR-J2S 서보드라이브)")
        print("  Mitsubishi / MELSEC-FX5U   (FX5U PLC)")
        print("  LS / SV-iG5A               (LS 인버터)")
        print("  Autonics / E-Series        (오토닉스 인코더)")
        return

    pdf_path = sys.argv[1]
    manufacturer = sys.argv[2]
    series = sys.argv[3]

    if not os.path.exists(pdf_path):
        print(f"파일을 찾을 수 없습니다: {pdf_path}")
        return

    print(f"\n=== 매뉴얼 업로드 시작 ===")
    print(f"파일: {pdf_path}")
    print(f"제조사: {manufacturer}")
    print(f"시리즈: {series}")
    print()

    from app.db.database import init_db, async_session
    from app.services.pdf_processor import process_manual_pdf

    await init_db()

    with open(pdf_path, 'rb') as f:
        pdf_bytes = f.read()

    print(f"파일 크기: {len(pdf_bytes) / 1024 / 1024:.1f}MB")
    print("처리 중... (시간이 걸릴 수 있습니다)")

    async with async_session() as db:
        result = await process_manual_pdf(pdf_bytes, manufacturer, series, db)

    print(f"\n=== 처리 결과 ===")
    print(f"총 페이지: {result['pages']}페이지")
    print(f"발견된 모델: {len(result['products_found'])}개 → {result['products_found'][:5]}")
    print(f"스펙 저장: {result['specs_saved']}개")
    print(f"알람코드 저장: {result['alarms_saved']}개")
    if result['errors']:
        print(f"오류: {result['errors'][:3]}")
    print("\n✅ 완료!")


if __name__ == "__main__":
    asyncio.run(main())
