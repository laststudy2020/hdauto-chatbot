"""수신자 자가등록 설정 점검 도구.

## 확인할 수 있는 것 / 없는 것

카카오 `/oauth/authorize`는 **로그인 전에 어떤 파라미터도 검증하지 않는다.**
2026-07-30 실측: client_id를 0으로 채워도, redirect_uri를 미등록 도메인으로 넣어도,
scope를 존재하지 않는 값으로 넣어도 전부 동일하게 `accounts.kakao.com/login`으로
302한다. `/oauth/token`도 code를 먼저 검증해 KOE320만 돌려주므로 redirect_uri 등록
여부가 드러나지 않는다.

따라서 **콘솔 설정(Redirect URI 등록, 동의항목, 앱 배포 상태)은 이 스크립트로 검증할
수 없다.** 실제 카카오 계정으로 링크를 눌러 로그인·동의를 거쳐야만 드러난다. 그
단계에서 설정이 틀렸으면 브라우저에 KOE006(Redirect URI 미등록) 등이 표시된다.

이 스크립트가 실제로 확인하는 것은 **배포된 앱 쪽 설정**이다:

  1. 배포 앱이 살아 있는지
  2. /connect 인증 게이트가 닫혀 있는지 (키 없이 401)
  3. KAKAO_RECIPIENT_REDIRECT_URI가 배포 환경변수에 반영됐는지 (503이면 미설정)
  4. 앱이 만들어내는 redirect_uri가 콜백 경로와 일치하는지

그리고 사람이 눌러야 할 **초대 링크를 출력**한다.

## 실행

    # 배포 앱 점검 + 초대 링크 출력
    python check_kakao_console_setup.py https://내도메인.onrender.com --name 홍길동

    # 링크를 눌러 동의한 뒤, 실제로 등록됐는지 DB에서 확인
    python check_kakao_console_setup.py --confirm 홍길동
"""

import argparse
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

import httpx
from sqlalchemy import select

from app.config import get_settings

settings = get_settings()

CALLBACK_PATH = "/api/admin/recipients/callback"
CONNECT_PATH = "/api/admin/recipients/connect"

results: list[tuple[bool, str]] = []


def check(ok, label: str, detail: str = ""):
    results.append((bool(ok), label))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    if detail:
        for line in detail.splitlines():
            print(f"       {line}")


async def check_deployment(base_url: str, name: str, admin_key: str | None = None) -> None:
    base = base_url.rstrip("/")
    expected_uri = f"{base}{CALLBACK_PATH}"
    # 배포 환경의 ADMIN_COMMAND_KEY는 로컬 .env와 다를 수 있다(프로덕션은 별도 키를
    # 쓰는 게 정상). --key로 넘기면 로컬 .env를 고치지 않고 점검할 수 있다.
    admin_key = admin_key or settings.ADMIN_COMMAND_KEY

    print(f"=== 배포 앱 점검: {base} ===")

    async with httpx.AsyncClient(trust_env=False, timeout=30.0,
                                 follow_redirects=False) as client:
        try:
            r = await client.get(f"{base}/health")
            check(r.status_code == 200, f"앱 응답 (/health → {r.status_code})")
        except Exception as e:
            check(False, f"앱 접속 실패: {e}",
                  "도메인이 맞는지, Render 서비스가 깨어 있는지 확인하세요 "
                  "(무료 플랜은 첫 요청이 느립니다).")
            return

        r = await client.get(f"{base}{CONNECT_PATH}", params={"name": name})
        check(r.status_code == 401,
              f"키 없이 /connect → 401 (실제 {r.status_code})",
              "" if r.status_code == 401 else
              "인증 게이트가 열려 있습니다 — 누구나 수신자로 등록될 수 있어 즉시 확인이 필요합니다.")

        if not admin_key:
            check(False, "ADMIN_COMMAND_KEY 설정됨",
                  "로컬 .env에 없어 정상 흐름을 확인할 수 없습니다.")
            return

        r = await client.get(f"{base}{CONNECT_PATH}",
                             params={"key": admin_key, "name": name})

        if r.status_code == 503:
            check(False, "KAKAO_RECIPIENT_REDIRECT_URI가 배포 환경에 반영됨",
                  "Render 환경변수에 아래 값을 추가하고 재배포하세요.\n"
                  f"  KAKAO_RECIPIENT_REDIRECT_URI={expected_uri}")
            return
        if r.status_code == 401:
            check(False, "정상 /connect → 302",
                  "쓰고 있는 ADMIN_COMMAND_KEY가 배포 환경변수 값과 다릅니다.\n"
                  "Render 대시보드 > Environment에서 값을 확인해 --key로 넘기세요:\n"
                  f"  python {sys.argv[0]} {base} --name {name} --key <프로덕션키>\n"
                  "참고: /api/admin/* 전체가 같은 키를 쓰므로 이 증상은 새 엔드포인트에\n"
                  "국한된 것이 아닙니다.")
            return

        check(r.status_code == 302, f"정상 /connect → 302 (실제 {r.status_code})")
        if r.status_code != 302:
            print(f"       응답 본문: {r.text[:300]}")
            return

        location = r.headers.get("location", "")
        deployed_uri = httpx.URL(location).params.get("redirect_uri", "")
        match = deployed_uri == expected_uri
        check(match, "앱이 만드는 redirect_uri가 콜백 경로와 일치",
              "" if match else
              f"앱이 쓰는 값: {deployed_uri}\n"
              f"기대하는 값: {expected_uri}\n"
              "Render 환경변수를 기대값으로 고치세요. 콘솔에 등록한 값도 이것과 같아야 합니다.")

        invite = f"{base}{CONNECT_PATH}?key={admin_key}&name={httpx.QueryParams({'n': name})['n']}"
        print("\n" + "─" * 60)
        print("여기까지가 프로그램으로 확인할 수 있는 범위입니다.")
        print("콘솔 설정(Redirect URI 등록 / 동의항목 / 앱 배포 상태)은 실제 카카오")
        print("계정으로 로그인·동의를 거쳐야만 드러납니다. 아래 링크를 눌러보세요.\n")
        print(f"  {invite}\n")
        print("결과 해석:")
        print("  · 카카오 로그인 → 동의 → '✅ 등록 완료' 화면  → 설정 정상")
        print(f"  · KOE006                                    → 콘솔 Redirect URI에 "
              f"{expected_uri} 미등록")
        print("  · '앱 관리자 또는 팀원만 로그인 가능' 안내     → 앱이 '개발 중' 상태. "
              "해당 계정을 팀원 등록하거나 배포 상태로 전환")
        print("  · 동의항목 관련 오류                          → 콘솔 > 카카오 로그인 > "
              "동의항목에서 '카카오톡 메시지 전송' 사용 설정")
        print("\n동의를 마친 뒤 실제로 등록됐는지 확인:")
        print(f"  python check_kakao_console_setup.py --confirm {name}")
        print("─" * 60)


async def confirm_registration(name: str) -> None:
    """링크를 눌러 동의한 뒤 alarm_recipients에 행이 생겼는지 확인한다."""
    from app.db.database import async_session
    from app.db.models import AlarmRecipient

    print(f"=== 등록 확인: '{name}' ===")
    async with async_session() as db:
        rows = (await db.execute(
            select(AlarmRecipient).order_by(AlarmRecipient.id)
        )).scalars().all()

        print(f"전체 수신자 {len(rows)}명")
        for r in rows:
            print(f"  #{r.id} {r.name} channel={r.channel} active={r.is_active} "
                  f"refresh_token={'있음' if r.channel_token else '없음'} "
                  f"등록={r.created_at}")

        target = [r for r in rows if r.name == name]
        check(len(target) == 1, f"'{name}' 수신자 1행 존재 (실제 {len(target)}행)")
        if target:
            row = target[0]
            check(bool(row.channel_token), f"'{name}'의 refresh_token 저장됨")
            check(bool(row.is_active), f"'{name}'이 활성 상태")
            check(row.channel == "kakao", f"'{name}'의 channel=kakao")

        active = [r for r in rows if r.is_active and r.channel == "kakao"]
        print(f"\n활성 카카오 수신자 {len(active)}명 — 재고 알림은 이 인원 전원에게 발송됩니다.")
        if len(active) >= 2:
            print("다중 발송 최종 확인은 test_multi_recipient_notify.py로 하세요 "
                  "(실제 카톡 메시지가 발송됩니다).")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url", nargs="?", help="배포 도메인 (예: https://xxx.onrender.com)")
    ap.add_argument("--name", default="테스트수신자", help="초대할 수신자 이름")
    ap.add_argument("--key", help="배포 환경의 ADMIN_COMMAND_KEY (로컬 .env와 다를 때)")
    ap.add_argument("--confirm", metavar="이름",
                    help="링크를 눌러 동의한 뒤 등록 결과를 DB에서 확인")
    args = ap.parse_args()

    if args.confirm:
        await confirm_registration(args.confirm)
    elif args.base_url:
        await check_deployment(args.base_url, args.name, args.key)
    else:
        ap.error("배포 도메인 또는 --confirm 중 하나는 필요합니다.")

    failed = [label for ok, label in results if not ok]
    if results:
        print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
        if failed:
            print("실패 항목:")
            for label in failed:
                print(f"  · {label}")


if __name__ == "__main__":
    asyncio.run(main())
