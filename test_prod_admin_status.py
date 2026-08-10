"""프로덕션 부가 상태 점검 — 카카오 알림 가능 여부 / 아웃바운드 IP.

재고 문의를 실제로 던지면 관리자에게 카카오 알림이 나가므로, 챗봇을 거치지
않고 관리자 엔드포인트로만 확인한다. ADMIN_KEY 환경변수로 키를 넘긴다.

  $env:ADMIN_KEY = "..." ; python test_prod_admin_status.py
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import httpx

URL = "https://hdauto-chatbot.onrender.com"
KEY = os.environ.get("ADMIN_KEY", "")


def main() -> None:
    if not KEY:
        print("ADMIN_KEY 미설정 — 프로덕션 관리자 키를 넣고 다시 실행하세요.")
        sys.exit(2)

    with httpx.Client(timeout=60.0) as c:
        for path in ("/api/admin/myip", "/api/admin/kakao-status"):
            try:
                r = c.get(f"{URL}{path}", headers={"X-Admin-Key": KEY})
                print(f"{path} → {r.status_code}\n  {r.text[:600]}\n")
            except Exception as e:
                print(f"{path} → 실패 {type(e).__name__}: {e}\n")


if __name__ == "__main__":
    main()
