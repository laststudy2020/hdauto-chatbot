"""실서비스(Render) LS 신형 인버터 응답 검증.

배포된 인스턴스에 실제 HTTP로 물어보고, 답이 DB 기반인지(source)와 매뉴얼
내용이 실려 있는지 확인한다. 로컬 test_ls_manual_ingest.py가 "DB와 코드가
맞는가"를 본다면 이 스크립트는 "배포된 것이 그렇게 동작하는가"를 본다.

  python test_live_s100.py            # 바로 실행
  python test_live_s100.py --wait     # 배포 반영될 때까지 기다렸다 실행
  python test_live_s100.py --url http://localhost:8000

주의: 실제 CLOVA를 태우므로 호출 비용이 든다. 재고 문의는 넣지 않았다 —
관리자에게 카카오 알림이 나가기 때문이다.
"""

import argparse
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import httpx

DEFAULT_URL = "https://hdauto-chatbot.onrender.com"
TIMEOUT = 60.0

# 배포 판별용 프로브. LSLV 형명 패턴은 이번 배포에서 들어갔고 DB와 무관하므로,
# model_name이 채워지면 새 코드가 떠 있는 것이다(데이터는 이미 들어가 있음).
PROBE = "LSLV0022S100-2 외형 치수랑 중량 알려주세요"

# (질문, 기대 intent, 기대 source 후보, 답변에 있어야 할 조각)
CASES = [
    (
        "S100 인버터에 OLT 트립이 떴는데 어떻게 하나요?",
        "alarm", {"db_alarm"},
        ["과부하", "Pr."],          # S100 매뉴얼 파라미터 표기(iG5A는 F57)
    ),
    (
        "LSLV0022S100-2 외형 치수랑 중량 알려주세요",
        "specs", {"db"},
        ["100", "128", "145", "5.4"],
    ),
    (
        "S100 인버터 OVT 뜨는 이유가 뭔가요",
        "alarm", {"db_alarm"},
        ["과전압", "전압"],
    ),
    (
        "H100에 Ground Trip 떠요",
        "alarm", {"db_alarm"},
        ["지락", "출력"],
    ),
    (
        "SV015iG5A-4 OLt 떴습니다",
        "alarm", {"db_alarm"},
        ["F57"],                     # 신형 적재 후에도 기존 iG5A 답이 유지되는가
    ),
]

passed = failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"    PASS  {label}")
    else:
        failed += 1
        print(f"    FAIL  {label}   {detail}")


def ask(client: httpx.Client, url: str, message: str) -> dict:
    r = client.post(
        f"{url}/api/chat/",
        json={"message": message, "user_id": "live-test", "channel": "web"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def wait_for_deploy(client: httpx.Client, url: str, minutes: int = 12) -> bool:
    """새 코드가 떴는지 프로브로 확인. Render 첫 요청은 콜드스타트가 있다."""
    deadline = time.time() + minutes * 60
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            data = ask(client, url, PROBE)
            if data.get("model_name") == "LSLV0022S100-2":
                print(f"  배포 반영 확인 (시도 {attempt}회)")
                return True
            print(f"  아직 이전 코드 (model_name={data.get('model_name')!r}) — 30초 후 재확인")
        except Exception as e:
            print(f"  응답 없음 ({type(e).__name__}) — 30초 후 재확인")
        time.sleep(30)
    print("  제한 시간 내에 배포가 확인되지 않았습니다.")
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--wait", action="store_true", help="배포 반영까지 대기")
    args = ap.parse_args()
    url = args.url.rstrip("/")

    print(f"대상: {url}")
    with httpx.Client() as client:
        try:
            h = client.get(f"{url}/health", timeout=TIMEOUT).json()
            print(f"  /health → {h}")
        except Exception as e:
            print(f"  /health 실패: {e}")
            sys.exit(1)

        if args.wait and not wait_for_deploy(client, url):
            sys.exit(1)

        for message, want_intent, want_sources, fragments in CASES:
            print(f"\n  Q: {message}")
            try:
                data = ask(client, url, message)
            except Exception as e:
                check("응답 수신", False, f"{type(e).__name__}: {e}")
                continue

            reply = data.get("reply", "")
            print(f"     intent={data.get('intent')} source={data.get('source')} "
                  f"model={data.get('model_name')}")
            print(f"     {reply[:150].replace(chr(10), ' ')}...")

            check(f"intent={want_intent}", data.get("intent") == want_intent,
                  str(data.get("intent")))
            check(f"source∈{sorted(want_sources)}", data.get("source") in want_sources,
                  str(data.get("source")))
            missing = [f for f in fragments if f not in reply]
            check(f"매뉴얼 내용 반영 {fragments}", not missing, f"누락={missing}")

    print(f"\n{'=' * 74}")
    print(f"결과: {passed} PASS / {failed} FAIL")
    print("=" * 74)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
