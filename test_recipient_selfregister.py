"""수신자 자가등록 엔드포인트 검증 (2단계).

카카오 개발자콘솔 설정 없이 검증 가능한 범위만 다룬다:
  - /connect 관리자 키 게이트 (없음/오답/정답)
  - 리다이렉트 URI 미설정 시 fail-closed
  - 카카오 동의 화면으로의 302 + 쿼리 파라미터
  - state 논스의 1회용성과 30분 만료
  - /callback 위조/누락 state 차단
  - 토큰 교환을 가짜로 대체한 상태에서 alarm_recipients upsert

실제 카카오 동의 화면 왕복은 콘솔에 배포 도메인 Redirect URI가 등록된 뒤에만
가능하므로 이 스크립트 범위 밖이다.

전체를 하나의 이벤트 루프에서 돌린다 — TestClient(별도 루프)와 asyncio.run을 섞으면
asyncmy 커넥션이 다른 루프에서 재사용되어 "network operation failed" 로 죽는다.

주의: .env가 프로덕션 MariaDB를 가리킨다. 임시 수신자 행을 만들고 finally에서
지운다. 정리 실패 시 마지막에 경고를 크게 출력한다.
"""

import asyncio
import sys
import time
from urllib.parse import parse_qs, urlparse

sys.stdout.reconfigure(encoding="utf-8")

import httpx
from sqlalchemy import select

from app.api import recipients
from app.config import get_settings
from app.db.database import async_session
from app.db.models import AlarmRecipient
from app.main import app

settings = get_settings()

TEST_NAME = "__테스트_자가등록__"
FAIL_NAME = TEST_NAME + "_실패"
CALLBACK_URI = "https://example.invalid/api/admin/recipients/callback"

FAKE_TOKEN = {
    "access_token": "fake-access-token",
    "refresh_token": "fake-refresh-token",
    "expires_in": 21599,
    "refresh_token_expires_in": 5183999,
}

CONNECT = "/api/admin/recipients/connect"
CALLBACK = "/api/admin/recipients/callback"

results: list[tuple[bool, str]] = []


def check(cond, label: str):
    results.append((bool(cond), label))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")


async def _rows(name: str) -> list[AlarmRecipient]:
    async with async_session() as db:
        return list((await db.execute(
            select(AlarmRecipient).where(AlarmRecipient.name == name)
        )).scalars().all())


async def _cleanup() -> bool:
    """임시 수신자 행 제거. 남으면 프로덕션 알림이 매번 이 행에서 실패한다."""
    async with async_session() as db:
        rows = (await db.execute(
            select(AlarmRecipient).where(AlarmRecipient.name.in_([TEST_NAME, FAIL_NAME]))
        )).scalars().all()
        for row in rows:
            await db.delete(row)
        if rows:
            await db.commit()
        left = (await db.execute(
            select(AlarmRecipient).where(AlarmRecipient.name.in_([TEST_NAME, FAIL_NAME]))
        )).scalars().all()
    return not left


async def _state_from_connect(client: httpx.AsyncClient, admin_key: str, name: str) -> str:
    r = await client.get(CONNECT, params={"key": admin_key, "name": name})
    assert r.status_code == 302, f"/connect가 302를 주지 않음: {r.status_code}"
    return parse_qs(urlparse(r.headers["location"]).query)["state"][0]


async def main():
    admin_key = settings.ADMIN_COMMAND_KEY
    if not admin_key:
        print("ADMIN_COMMAND_KEY가 없습니다 — .env를 확인하세요.")
        return

    orig_redirect = settings.KAKAO_RECIPIENT_REDIRECT_URI
    orig_exchange = recipients._exchange_code_for_token
    cleaned = False

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            # ── 1) 관리자 키 게이트 ──
            r = await client.get(CONNECT, params={"name": TEST_NAME})
            check(r.status_code == 401, f"키 없이 /connect → 401 (실제 {r.status_code})")

            r = await client.get(CONNECT, params={"key": "wrong-key", "name": TEST_NAME})
            check(r.status_code == 401, f"틀린 키로 /connect → 401 (실제 {r.status_code})")

            # ── 2) 리다이렉트 URI 미설정이면 fail-closed ──
            settings.KAKAO_RECIPIENT_REDIRECT_URI = ""
            r = await client.get(CONNECT, params={"key": admin_key, "name": TEST_NAME})
            check(r.status_code == 503,
                  f"Redirect URI 미설정 시 /connect → 503 (실제 {r.status_code})")

            # ── 3) 정상 발급: 카카오 동의 화면으로 302 ──
            settings.KAKAO_RECIPIENT_REDIRECT_URI = CALLBACK_URI
            r = await client.get(CONNECT, params={"key": admin_key, "name": TEST_NAME})
            check(r.status_code == 302, f"정상 /connect → 302 (실제 {r.status_code})")

            location = r.headers.get("location", "")
            parsed = urlparse(location)
            qs = parse_qs(parsed.query)
            print(f"  → {parsed.netloc}{parsed.path}")
            check(parsed.netloc == "kauth.kakao.com" and parsed.path == "/oauth/authorize",
                  "카카오 동의 화면 URL로 리다이렉트")
            check(qs.get("client_id", [""])[0] == settings.KAKAO_REST_API_KEY,
                  "client_id가 KAKAO_REST_API_KEY와 일치")
            check(qs.get("redirect_uri", [""])[0] == CALLBACK_URI,
                  "redirect_uri가 설정값과 일치")
            check(qs.get("response_type", [""])[0] == "code", "response_type=code")
            check("talk_message" in qs.get("scope", [""])[0],
                  f"scope에 talk_message 포함 (실제 {qs.get('scope')})")

            state = qs.get("state", [""])[0]
            check(len(state) >= 20, f"state 논스가 충분히 길다 (길이 {len(state)})")
            check(TEST_NAME not in location, "동의 화면 URL에 수신자 이름 미노출")
            check(admin_key not in location, "동의 화면 URL에 ADMIN_COMMAND_KEY 미노출")

            # ── 4) 논스 1회용성 ──
            s1 = recipients._issue_nonce("일회용테스트")
            check(recipients._consume_nonce(s1) == "일회용테스트", "논스 소진 시 이름 반환")
            check(recipients._consume_nonce(s1) is None, "같은 논스 재사용 시 None")

            # ── 5) 만료 ──
            s2 = recipients._issue_nonce("만료테스트")
            recipients._nonces[s2]["expires_at"] = time.time() - 1
            check(recipients._consume_nonce(s2) is None, "만료된 논스는 None")
            check(s2 not in recipients._nonces, "만료된 논스는 저장소에서 제거됨")

            # ── 6) 위조/누락 state는 /callback에서 거절 ──
            r = await client.get(CALLBACK, params={"code": "any", "state": "forged-value"})
            check(r.status_code == 400, f"위조 state → 400 (실제 {r.status_code})")

            r = await client.get(CALLBACK, params={"code": "any"})
            check(r.status_code == 422, f"state 누락 → 422 (실제 {r.status_code})")

            # ── 7) 정상 콜백: 토큰 교환을 가짜로 대체하고 upsert 확인 ──
            seen_codes = []

            async def fake_exchange(code: str) -> dict:
                seen_codes.append(code)
                return dict(FAKE_TOKEN)

            recipients._exchange_code_for_token = fake_exchange

            state = await _state_from_connect(client, admin_key, TEST_NAME)
            r = await client.get(CALLBACK, params={"code": "test-auth-code", "state": state})
            check(r.status_code == 200, f"정상 콜백 → 200 (실제 {r.status_code})")
            check(seen_codes == ["test-auth-code"],
                  f"콜백이 받은 code를 그대로 교환에 사용 (실제 {seen_codes})")
            check(TEST_NAME in r.text, "완료 화면에 등록된 이름 표시")

            rows = await _rows(TEST_NAME)
            check(len(rows) == 1, f"수신자 1행 생성 (실제 {len(rows)}행)")
            first_id = None
            if rows:
                row = rows[0]
                first_id = row.id
                print(f"  생성된 행: id={row.id} channel={row.channel} active={row.is_active}")
                check(row.channel == "kakao", "channel=kakao")
                check(row.channel_token == FAKE_TOKEN["refresh_token"],
                      "channel_token에 refresh_token 저장")
                check(row.access_token == FAKE_TOKEN["access_token"], "access_token 캐시 저장")
                check(row.token_expires_in == FAKE_TOKEN["expires_in"], "token_expires_in 저장")
                check(row.token_obtained_at is not None, "token_obtained_at 기록")
                check(bool(row.is_active), "is_active=True")

            # ── 8) 같은 이름 재등록 → 행이 늘지 않고 토큰만 갱신 ──
            async def fake_exchange2(code: str) -> dict:
                return {**FAKE_TOKEN, "refresh_token": "fake-refresh-token-2"}

            recipients._exchange_code_for_token = fake_exchange2
            state = await _state_from_connect(client, admin_key, TEST_NAME)
            r = await client.get(CALLBACK, params={"code": "test-auth-code", "state": state})
            check(r.status_code == 200, f"재등록 콜백 → 200 (실제 {r.status_code})")

            rows = await _rows(TEST_NAME)
            check(len(rows) == 1, f"재등록 후에도 1행 유지 (실제 {len(rows)}행)")
            if rows:
                check(rows[0].id == first_id, "같은 행을 갱신 (id 동일)")
                check(rows[0].channel_token == "fake-refresh-token-2",
                      "refresh_token이 새 값으로 갱신됨")

            # ── 9) refresh_token 없는 응답은 거절 ──
            async def no_refresh(code: str) -> dict:
                return {"access_token": "a", "expires_in": 100}

            recipients._exchange_code_for_token = no_refresh
            state = await _state_from_connect(client, admin_key, FAIL_NAME)
            r = await client.get(CALLBACK, params={"code": "x", "state": state})
            check(r.status_code == 400, f"refresh_token 없는 응답 → 400 (실제 {r.status_code})")
            check(not await _rows(FAIL_NAME), "refresh_token 없으면 행 미생성")

            # ── 10) 토큰 교환 실패 시 행이 만들어지지 않는다 ──
            async def failing_exchange(code: str) -> dict:
                raise RuntimeError("KOE320: code already used")

            recipients._exchange_code_for_token = failing_exchange
            state = await _state_from_connect(client, admin_key, FAIL_NAME)
            r = await client.get(CALLBACK, params={"code": "bad", "state": state})
            check(r.status_code == 400, f"토큰 교환 실패 → 400 (실제 {r.status_code})")
            check(not await _rows(FAIL_NAME), "교환 실패 시 수신자 행 미생성")

            # ── 11) 동의 거절(error 파라미터)은 200 안내 + 행 미생성 ──
            recipients._exchange_code_for_token = fake_exchange
            state = await _state_from_connect(client, admin_key, FAIL_NAME)
            r = await client.get(CALLBACK, params={"state": state, "error": "access_denied"})
            check(r.status_code == 200, f"동의 거절 → 200 안내 (실제 {r.status_code})")
            check("취소" in r.text, "거절 시 취소 안내 문구")
            check(not await _rows(FAIL_NAME), "거절 시 행 미생성")

        finally:
            settings.KAKAO_RECIPIENT_REDIRECT_URI = orig_redirect
            recipients._exchange_code_for_token = orig_exchange
            try:
                cleaned = await _cleanup()
            except Exception as e:
                print(f"\n정리 중 예외: {e}")
                cleaned = False

    print()
    if cleaned:
        print("임시 수신자 정리 완료")
    else:
        print("!!! 경고: 임시 수신자가 남아 있을 수 있습니다 !!!")
        print(f"!!! alarm_recipients에서 name IN ('{TEST_NAME}', '{FAIL_NAME}') 행을 지우세요 !!!")

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
    if failed:
        print("실패 항목:")
        for label in failed:
            print(f"  · {label}")
    print("\n결과:", "통과" if not failed and cleaned else "실패")


if __name__ == "__main__":
    asyncio.run(main())
