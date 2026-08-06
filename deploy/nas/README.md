# Render → 시놀로지 NAS 이전 절차

대상: 시놀로지 **DS225+** (`hdautonas`, Tailscale `100.109.19.49`)

## 왜 옮기는가

1. **콜드 스타트 제거.** Render 무료 플랜은 15분 놀면 잠든다. 2026-08-06 실측
   기동 시간 **161초** — 고객이 톡톡으로 물으면 3분 뒤에 답이 간다.
2. **경로 단순화.** DB는 이미 이 NAS에 있다. Render는 거기 닿으려고
   `tailscaled(userspace) → SOCKS5 → tailscale_proxy.py → tailnet → NAS:3306`를
   거쳤다. NAS에서 돌면 `127.0.0.1:3306`이 된다. `start.sh`와
   `tailscale_proxy.py`는 NAS 배포에서 쓰이지 않는다.
3. **가용성이 나빠지지 않는다.** NAS가 죽으면 지금도 챗봇은 죽는다(DB가 거기
   있으므로). 새로 생기는 의존성은 사무실 인터넷 회선뿐이다.

## 준비물 확인

DSM에 로그인한 뒤(`http://100.109.19.49:5000`):

- **패키지 센터 → Container Manager** 설치되어 있을 것
- **패키지 센터 → Tailscale** 설치되어 있을 것 (이미 tailnet에 붙어 있으므로 설치됨)
- **제어판 → 터미널 및 SNMP → SSH 서비스 활성화** (포트 22 열려 있음을 확인함)

---

## 1단계 — Render의 환경변수를 확보한다

**로컬 `.env`를 그대로 쓰면 안 된다.** 운영값과 다른 키가 있다(최소한
`ADMIN_COMMAND_KEY`). 로컬에는 아예 없는 키도 있다(`KAKAO_REST_API_KEY`,
`KAKAO_RECIPIENT_REDIRECT_URI`, `ADMIN_COMMAND_KEY`).

Render 대시보드 → 해당 서비스 → **Environment** 에서 전체 목록을 연다.
그 값을 기준으로 NAS의 `.env`를 만든다.

### 그대로 옮기는 키

```
CLOVA_API_KEY  CLOVA_HOST  CLOVA_MODEL
COMPANY_NAME  COMPANY_ADDRESS  COMPANY_PHONE  COMPANY_LAT  COMPANY_LNG  COMPANY_HOURS
TALKTALK_AUTHORIZATION  ADMIN_TALKTALK_USER_ID
NAVER_CLIENT_ID  NAVER_CLIENT_SECRET
NAVER_SHOPPING_CLIENT_ID  NAVER_SHOPPING_CLIENT_SECRET
NAVER_COMMERCE_CLIENT_ID  NAVER_COMMERCE_CLIENT_SECRET  NAVER_COMMERCE_ENABLED
KAKAO_REST_API_KEY  KAKAO_CLIENT_SECRET
ADMIN_COMMAND_KEY
DEFAULT_STOCK_THRESHOLD  PRICE_DIFF_THRESHOLD  SLACK_WEBHOOK_URL
```

### 버리는 키 (tailnet 우회용이었다)

```
TS_AUTHKEY  NAS_TAILSCALE_IP  NAS_DB_PORT
TS_PROXY_LISTEN_PORT  TS_SOCKS5_PORT  TS_PROXY_CONNECT_TIMEOUT
```

### 바꾸는 키

| 키 | Render | NAS |
|---|---|---|
| `DATABASE_URL` | `...@127.0.0.1:13306/...` (포워더 경유) | `...@127.0.0.1:3306/...` |
| `KAKAO_RECIPIENT_REDIRECT_URI` | `https://hdauto-chatbot.onrender.com/api/admin/recipients/callback` | `https://hdautonas.tailfbb0ed.ts.net/api/admin/recipients/callback` |

### 새로 넣는 키

```
RUNTIME_ENV=nas
ENABLE_MANUAL_UPLOAD=false     # 메모리 여유 확인 후 true로 올릴 것
TALKTALK_WEBHOOK_KEY=<아래에서 생성>
```

`TALKTALK_WEBHOOK_KEY`는 톡톡 웹훅을 여는 열쇠다(2026-07-28부터 채널이 막혀
있는 원인). 아무 난수나 쓰면 된다:

```bash
openssl rand -hex 24
```

---

## 2단계 — NAS에 코드를 놓는다

```bash
ssh <DSM관리자계정>@100.109.19.49

sudo mkdir -p /volume1/docker/hdauto-chatbot
cd /volume1/docker
sudo git clone https://github.com/laststudy2020/hdauto-chatbot.git hdauto-chatbot
cd hdauto-chatbot
```

비공개 저장소라 인증을 물으면 GitHub **Personal Access Token**을 비밀번호
자리에 넣는다.

`.env`를 만든다 (1단계에서 정리한 내용):

```bash
sudo vi .env
sudo chmod 600 .env          # 비밀값이 들어 있다
```

---

## 3단계 — 띄운다

```bash
sudo docker compose up -d --build
sudo docker compose logs -f
```

DSM 버전에 따라 `docker compose`가 없으면 `docker-compose`를 쓴다.

로그에 아래가 보이면 정상이다:

```
DB 초기화 완료
매뉴얼 업로드 API 비활성화 (nas - 메모리 절약)
Uvicorn running on http://0.0.0.0:8000
```

확인:

```bash
curl -s http://127.0.0.1:8000/health          # {"status":"ok"}
curl -s http://127.0.0.1:8000/ | head -c 300  # "mode":"nas"
```

`DB 초기화` 단계에서 멈추면 `DATABASE_URL`의 host/port를 본다. NAS 안에서는
`13306`이 아니라 **`3306`**이다.

---

## 4단계 — 공개 HTTPS를 연다 (Tailscale Funnel)

네이버 톡톡과 카카오는 **바깥에서 우리 쪽으로 접속**한다. Tailscale에 붙어
있는 것만으로는 안 되고 공개 주소가 필요하다. 공유기를 건드리지 않아도 되는
Funnel을 쓴다.

```bash
TS=/var/packages/Tailscale/target/bin/tailscale

sudo $TS funnel --bg 8000
sudo $TS funnel status
```

첫 실행에서 tailnet 정책에 HTTPS/Funnel 허용이 필요하다는 안내가 나오면,
출력된 링크를 열어 활성화한 뒤 다시 실행한다.

이제 공개 주소는 이것이다:

```
https://hdautonas.tailfbb0ed.ts.net
```

바깥(휴대폰 LTE 등)에서 확인:

```
https://hdautonas.tailfbb0ed.ts.net/health
```

> **대안**: 공유기 포트포워딩이 가능하면 DDNS(`<이름>.synology.me`) +
> DSM 내장 Let's Encrypt + 역방향 프록시(→ `localhost:8000`)로 가도 된다.
> 앱 코드는 그대로고, 아래 5단계의 주소만 바뀐다.

---

## 5단계 — 외부 서비스에 새 주소를 등록한다

### 네이버 톡톡 (지금 막혀 있는 채널을 여는 단계)

파트너센터 → 개발자도구 → 챗봇API → Webhook URL:

```
https://hdautonas.tailfbb0ed.ts.net/api/talktalk/webhook?k=<TALKTALK_WEBHOOK_KEY와 같은 값>
```

### 카카오

개발자콘솔 → 내 애플리케이션 → 카카오 로그인 → Redirect URI **추가**
(기존 Render 것은 롤백 대비로 지우지 말 것):

```
https://hdautonas.tailfbb0ed.ts.net/api/admin/recipients/callback
```

---

## 6단계 — 검증

로컬 PC에서:

```bash
python test_talktalk_live.py --check --url https://hdautonas.tailfbb0ed.ts.net
```

`인증이 설정돼 있다(정상)`이 나와야 한다.

응답 품질 회귀:

```bash
python test_live_s100.py --url https://hdautonas.tailfbb0ed.ts.net
```

마지막으로 톡톡 대화창에서 직접:

```
S100 인버터에 OLT 트립이 떴는데 어떻게 하나요?
LSLV0022S100-2 외형 치수랑 중량 알려주세요
LSLV0022G100-4 사양 알려주세요        ← "미등록"이라 답해야 정상
```

---

## 7단계 — Render 정리 (며칠 병행 후에)

NAS가 안정적으로 응답하는 걸 확인하기 전에는 **Render를 내리지 않는다.**
문제가 생기면 파트너센터 웹훅 URL만 Render 주소로 되돌리면 즉시 복구된다.

안정화되면 Render 서비스를 suspend 한다. Tailscale 콘솔에서
`hdauto-render-1` 노드와 해당 auth key도 함께 정리한다.

---

## 운영 명령

```bash
cd /volume1/docker/hdauto-chatbot

sudo docker compose logs -f --tail=100    # 로그
sudo docker compose restart               # 재시작
sudo docker compose down                  # 정지

sudo git pull && sudo docker compose up -d --build   # 배포(업데이트)
```

컨테이너는 `restart: unless-stopped`라 NAS 재부팅 후 자동으로 다시 뜬다.

## 알아둘 것

- **`network_mode: host`를 쓴다.** MariaDB가 시놀로지 패키지든 별도 컨테이너든
  호스트 3306에 노출돼 있어서, host 모드면 어느 쪽이든 `127.0.0.1:3306`으로
  붙는다. 이 때문에 compose의 `ports:` 매핑은 없다(host 모드에선 무시된다).
- **DSM이 5000/5001을 이미 쓴다.** 앱은 8000이라 겹치지 않는다.
- **`.env`는 이미지에 들어가지 않는다**(`.dockerignore`). 런타임에 `env_file`로
  주입된다. 이미지 레이어에 비밀값이 굳지 않는다.
