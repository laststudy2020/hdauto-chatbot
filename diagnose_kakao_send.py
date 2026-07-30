"""카카오 발송 실패 원인을 단계별로 좁힌다.

프로덕션 DB의 수신자 행(refresh_token)을 그대로 쓰고, 로컬 .env의 카카오 자격증명으로
토큰 갱신 → 발송을 순서대로 시도한다. 실패하면 카카오가 돌려준 원문을 그대로 보여준다.

단계:
  1. 수신자 행 상태 (refresh_token 유무, access_token 캐시 만료 여부)
  2. 토큰 갱신 (kauth /oauth/token, grant_type=refresh_token)
  3. 실제 발송 (kapi memo/default/send)
  4. get_kakao_notify_health() 결과

주의:
  · 로컬 .env의 KAKAO_REST_API_KEY / KAKAO_CLIENT_SECRET을 쓴다. Render 환경변수가
    다른 값이면 이 진단은 프로덕션과 다른 결과를 낼 수 있다.
  · 갱신에 성공하면 회전된 refresh_token이 프로덕션 DB에 저장된다 — 프로덕션이 하는
    동작과 동일하므로 의도된 것이다.
  · 3단계는 실제 카카오톡 메시지를 보낸다 (테스트 표기를 붙인다).

실행: python diagnose_kakao_send.py
"""

import asyncio
import logging
import sys

sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db.database import async_session
from app.db.models import AlarmRecipient
from app.services import admin_notify

settings = get_settings()

KAKAO_TOKEN_ERRORS = {
    "KOE320": "인가 코드 문제 (refresh_token 흐름에서는 나오지 않아야 함)",
    "KOE322": "refresh_token 만료 — 재인증 필요",
    "KOE303": "client_secret 불일치 — 콘솔에서 시크릿을 재발급/토글했을 때 발생",
    "KOE101": "client_id(REST API 키) 불일치",
    "KOE401": "앱과 토큰의 앱이 다름",
}


async def main():
    print(f"로컬 KAKAO_REST_API_KEY: {settings.KAKAO_REST_API_KEY[:6]}... "
          f"({len(settings.KAKAO_REST_API_KEY)}자)")
    print(f"로컬 KAKAO_CLIENT_SECRET: {'설정됨' if settings.KAKAO_CLIENT_SECRET else '없음'} "
          f"({len(settings.KAKAO_CLIENT_SECRET)}자)")

    async with async_session() as db:
        recipients = (await db.execute(
            select(AlarmRecipient).where(
                AlarmRecipient.is_active.is_(True),
                AlarmRecipient.channel == "kakao",
            ).order_by(AlarmRecipient.id)
        )).scalars().all()

        if not recipients:
            print("\n활성 카카오 수신자가 없습니다.")
            return

        for r in recipients:
            print(f"\n{'=' * 60}")
            print(f"수신자 #{r.id} {r.name}")
            print(f"  refresh_token: {'있음 (' + str(len(r.channel_token)) + '자)' if r.channel_token else '없음'}")
            print(f"  access_token 캐시: {'있음' if r.access_token else '없음'}")
            print(f"  token_expires_in: {r.token_expires_in}")
            print(f"  token_obtained_at: {r.token_obtained_at}")

            # ── 2단계: 토큰 갱신을 강제로 시도 (캐시를 우회해 원인을 본다) ──
            print("\n[2단계] refresh_token으로 토큰 갱신 시도")
            async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
                resp = await client.post(
                    f"{admin_notify.KAUTH_BASE}/oauth/token",
                    data={
                        "grant_type": "refresh_token",
                        "client_id": settings.KAKAO_REST_API_KEY,
                        "client_secret": settings.KAKAO_CLIENT_SECRET,
                        "refresh_token": r.channel_token,
                    },
                )
            print(f"  HTTP {resp.status_code}")
            if not resp.is_success:
                body = resp.text
                print(f"  응답: {body[:400]}")
                code = None
                for c in KAKAO_TOKEN_ERRORS:
                    if c in body:
                        code = c
                        break
                if code:
                    print(f"\n  >>> {code}: {KAKAO_TOKEN_ERRORS[code]}")
                else:
                    print("\n  >>> 알 수 없는 오류 — 위 응답 원문 확인")
                print("\n  토큰 갱신이 실패하므로 발송도 불가능합니다. 재인증이 필요합니다.")
                continue

            token = resp.json()
            print(f"  갱신 성공 — access_token 획득, expires_in={token.get('expires_in')}")
            rotated = bool(token.get("refresh_token"))
            print(f"  refresh_token 회전: {'있음' if rotated else '없음(기존 유지)'}")

            # 회전된 토큰을 저장하지 않으면 프로덕션이 다음 갱신에서 실패할 수 있다.
            r.access_token = token["access_token"]
            r.token_expires_in = token["expires_in"]
            from datetime import datetime
            r.token_obtained_at = datetime.utcnow()
            if rotated:
                r.channel_token = token["refresh_token"]
            await db.commit()
            print("  DB에 저장 완료")

            # ── 3단계: 실제 발송 ──
            print("\n[3단계] memo/default/send 발송 시도")
            ok = await admin_notify._send_kakao_text(
                db, r,
                "🔧 발송 진단 테스트\n"
                "이 메시지가 보이면 카카오 발송 경로는 정상입니다.\n"
                "(재고 알림 진단용 — 무시하셔도 됩니다)"
            )
            print(f"  발송 결과: {'성공' if ok else '실패'}")
            if ok:
                print("  >>> 카카오톡 '나와의 채팅'을 확인하세요.")

    print(f"\n{'=' * 60}")
    print(f"헬스 상태: {admin_notify.get_kakao_notify_health()}")


if __name__ == "__main__":
    asyncio.run(main())
