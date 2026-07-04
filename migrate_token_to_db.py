"""
1회성 스크립트: kakao_test/token.json에 있는 토큰을 실제 프로젝트 DB의
kakao_tokens 테이블로 옮긴다.

실행 위치: hdauto-chatbot 프로젝트 루트 (app 패키지를 import할 수 있는 위치)
사용법:
    python migrate_token_to_db.py [token.json 경로]
    (경로 생략 시 ./kakao_test/token.json 을 기본으로 찾음)
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from app.db.database import async_session
from app.db.models import KakaoToken


async def main():
    token_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("kakao_test/token.json")
    if not token_path.exists():
        print(f"파일을 찾을 수 없습니다: {token_path}")
        return

    token = json.loads(token_path.read_text())

    async with async_session() as db:
        existing = await db.get(KakaoToken, 1)
        if existing:
            existing.access_token = token["access_token"]
            existing.refresh_token = token["refresh_token"]
            existing.expires_in = token["expires_in"]
            existing.obtained_at = datetime.utcnow()
            print("기존 행을 업데이트했습니다.")
        else:
            db.add(KakaoToken(
                id=1,
                access_token=token["access_token"],
                refresh_token=token["refresh_token"],
                expires_in=token["expires_in"],
                obtained_at=datetime.utcnow(),
            ))
            print("새 행을 추가했습니다.")

        await db.commit()

    print("이관 완료.")


if __name__ == "__main__":
    asyncio.run(main())