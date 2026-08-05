"""톡톡 웹훅 인증 게이트 검증 (오프라인 — 발송/CLOVA 호출 없음).

이 게이트가 잘못 열리면 웹훅 URL을 아는 외부인이 위조 요청으로 CLOVA·톡톡
발송 API를 소모시킬 수 있고, 잘못 닫히면 실제 고객 메시지가 전부 403이 된다
(2026-07-28 b359ad9 이후 실제로 그랬다). 양쪽을 다 못박아 둔다.

  python test_talktalk_gate.py
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

# settings는 lru_cache라 import 전에 환경을 잡아야 한다.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./_gate_test.db"
os.environ["TALKTALK_WEBHOOK_KEY"] = "test-key-1234"
os.environ["TALKTALK_SECRET"] = ""
os.environ["DEBUG"] = "False"

from fastapi.testclient import TestClient  # noqa: E402

from app.api import talktalk as tt  # noqa: E402
from app.db.database import get_db  # noqa: E402
from app.main import app  # noqa: E402

passed = failed = 0
sent: list[tuple[str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}   {detail}")


async def _fake_send(user_id, text, authorization, quick_replies=None):
    sent.append((user_id, text))
    return 200


async def _fake_process(message, user_id, db):
    return f"[echo] {message}", "general"


async def _fake_db():
    yield None


def main() -> None:
    tt.send_to_talktalk = _fake_send
    tt._process_message = _fake_process
    app.dependency_overrides[get_db] = _fake_db

    body = {"event": "send", "user": "u1", "textContent": {"text": "S100 OLT"}}
    with TestClient(app) as c:
        r = c.post("/api/talktalk/webhook", json=body)
        check("키 없으면 403", r.status_code == 403, f"{r.status_code} {r.text[:80]}")

        r = c.post("/api/talktalk/webhook?k=wrong", json=body)
        check("키 틀리면 403", r.status_code == 403, f"{r.status_code} {r.text[:80]}")

        r = c.post("/api/talktalk/webhook?k=test-key-1234", json=body)
        check("키 맞으면 200", r.status_code == 200, f"{r.status_code} {r.text[:80]}")
        check("메시지가 라우팅돼 발송됨",
              len(sent) == 1 and sent[0][0] == "u1" and "S100 OLT" in sent[0][1],
              str(sent))

        # 인증이 통과되기 전에는 발송이 일어나면 안 된다.
        before = len(sent)
        c.post("/api/talktalk/webhook?k=wrong", json=body)
        check("거부된 요청은 발송하지 않음", len(sent) == before, str(sent))

    app.dependency_overrides.clear()
    print(f"\n결과: {passed} PASS / {failed} FAIL")
    for leftover in ("./_gate_test.db",):
        if os.path.exists(leftover):
            os.remove(leftover)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
