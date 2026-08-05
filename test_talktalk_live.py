"""네이버 톡톡 웹훅 실동작 검증.

웹훅은 응답을 HTTP로 돌려주지 않는다 — 처리 결과를 톡톡 발송 API로 **실제
사용자에게 보낸다**. 그래서 이 스크립트를 돌리면 지정한 user_id의 톡톡
대화창에 진짜 메시지가 도착한다. user_id 기본값을 두지 않은 이유다.

  # 1단계: 인증 상태만 확인 (발송 없음)
  python test_talktalk_live.py --check

  # 2단계: 실제 왕복 (본인 톡톡 user_id로만 쏠 것)
  python test_talktalk_live.py --user <톡톡 user_id> --key <웹훅 키>
  python test_talktalk_live.py --user <톡톡 user_id> --secret <서명 시크릿>

--user에 관리자 본인 id를 넣으면 본인 톡톡으로 답변이 온다. 고객 id를 넣으면
고객에게 발송되므로 넣지 말 것.
"""

import argparse
import hashlib
import hmac
import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import httpx

DEFAULT_URL = "https://hdauto-chatbot.onrender.com"
WEBHOOK_PATH = "/api/talktalk/webhook"

# 톡톡 대화창에서 눈으로 확인할 시나리오. 웹챗(test_live_s100.py)과 같은 질문을
# 톡톡 경로로 보내 두 채널이 같은 답을 내는지 본다.
SCENARIOS = [
    "S100 인버터에 OLT 트립이 떴는데 어떻게 하나요?",
    "G100 인버터에 Over Current1 트립이 떴습니다",
    "LSLV0022S100-2 외형 치수랑 중량 알려주세요",
    "LSLV0022G100-2 사양 알려주세요",
]


def check(url: str) -> None:
    """발송 없이 웹훅 인증 상태만 본다. 무서명/무키 호출은 403이어야 정상."""
    with httpx.Client(timeout=60.0) as c:
        print(f"/health → {c.get(f'{url}/health').json()}")
        r = c.post(f"{url}{WEBHOOK_PATH}",
                   json={"event": "send", "user": "probe",
                         "textContent": {"text": "ping"}})
        detail = ""
        try:
            detail = r.json().get("detail", "")
        except Exception:
            detail = r.text[:120]
        print(f"무인증 POST → {r.status_code} {detail}")
        if r.status_code != 403:
            print("  [주의] 403이 아니다 — 인증 없이 웹훅이 열려 있다.")
        elif "미설정" in detail:
            print("  → 아직 인증이 하나도 설정되지 않았다. 톡톡이 보낸 요청도"
                  " 전부 403으로 막힌다.")
            print("    Render 환경변수에 TALKTALK_SECRET 또는"
                  " TALKTALK_WEBHOOK_KEY를 넣어야 채널이 열린다.")
        else:
            print("  → 인증이 설정돼 있다(정상). 올바른 서명/키로만 통과한다.")


def send(url: str, user: str, message: str,
         key: str | None, secret: str | None) -> None:
    body = json.dumps(
        {"event": "send", "user": user, "textContent": {"text": message}},
        ensure_ascii=False,
    ).encode("utf-8")

    target = f"{url}{WEBHOOK_PATH}"
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-NAVER-BOT-Signature"] = hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
    if key:
        target += f"?k={key}"

    with httpx.Client(timeout=120.0) as c:
        r = c.post(target, content=body, headers=headers)
    ok = r.status_code == 200
    print(f"  [{'OK ' if ok else 'FAIL'}] {r.status_code}  {message}")
    if not ok:
        print(f"         {r.text[:160]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--check", action="store_true", help="발송 없이 인증 상태만 확인")
    ap.add_argument("--user", help="톡톡 user_id (본인 것만!)")
    ap.add_argument("--key", help="TALKTALK_WEBHOOK_KEY 값")
    ap.add_argument("--secret", help="TALKTALK_SECRET 값")
    args = ap.parse_args()
    url = args.url.rstrip("/")

    if args.check or not args.user:
        check(url)
        if not args.check:
            print("\n실제 왕복을 하려면 --user <본인 톡톡 user_id>와"
                  " --key 또는 --secret을 함께 주세요.")
        return

    if not (args.key or args.secret):
        print("--key 또는 --secret 중 하나가 필요합니다.")
        sys.exit(1)

    print(f"대상: {url}{WEBHOOK_PATH}")
    print(f"수신자: {args.user}")
    print(f"[알림] 아래 {len(SCENARIOS)}건이 이 사용자의 톡톡 대화창에 실제로 도착합니다.\n")
    for msg in SCENARIOS:
        send(url, args.user, msg, args.key, args.secret)
        time.sleep(2)  # 톡톡 발송 API 연속 호출 간격
    print("\n톡톡 대화창에서 답변 내용을 확인하세요.")


if __name__ == "__main__":
    main()
