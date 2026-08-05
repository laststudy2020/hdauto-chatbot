# 매뉴얼 PDF 폴더

이 폴더에 제조사 매뉴얼 PDF를 넣고 upload_manual.py로 처리하세요.

## LS 신형 인버터(S100/G100/H100)는 전용 스크립트를 쓴다

```
python ingest_ls_manual.py --dry-run     # 결과만 출력
python ingest_ls_manual.py               # S100 + G100 + H100 투입
```

범용 `upload_manual.py`(CLOVA 추출)를 이 세 매뉴얼에 쓰면 안 된다. 페이지 선별이
'고장'·'trip' 키워드 선착순이라 안전주의사항 페이지만 걷히고 정작 고장표
(S100 p.429~433)가 한 장도 안 들어온다. `ingest_ls_manual.py`는
`app/services/ls_manual_parser.py`로 좌표 기반 결정적 추출을 하며 LLM을 안 거친다.

### G100은 OCR이 필요하다

G100 PDF는 표의 영문·숫자가 텍스트 레이어에 있긴 해도 좌표가 실제 표 행과
전혀 다른 자리에 찍혀 있다(명칭 'Latch'가 명칭 열 x좌표에 오는 식). 그래서
`use_ocr=True`로 페이지를 렌더링해 다시 읽는다. 설치:

```
winget install -e --id UB-Mannheim.TesseractOCR
python -m pip install pytesseract
```

한국어 데이터(`kor`)는 필요 없다. **한글 설명은 텍스트 레이어 좌표가 맞으므로
그대로 쓰고, OCR은 영문·숫자만 채운다.** eng OCR이 한글을 'AHS' 같은 라틴
잡음으로 읽어 내므로, 한글 단어와 겹치는 OCR 결과는 버린다.

**G100은 숫자 사양(정격전류·중량·치수)을 넣지 않는다.** OCR이 소수점을 흘려
정격전류 2.5A를 25.0A로 읽는 게 확인됐고(p.291 실측 21개 값 중 16개만 정확),
10배 오차가 조용히 들어가는 편이 값이 없는 것보다 나쁘다. 용량(kW)과 전압
등급은 형명에서 확정되므로 그것만 채운다. 키패드 코드는 7세그먼트 그래픽이라
OCR이 못 읽어(0/5) 알람코드로 LCD 명칭을 쓴다.

## 사용법

1. 이 폴더에 PDF 파일 복사
2. 터미널에서 실행:

```
python upload_manual.py manuals/MR-J4매뉴얼.pdf Mitsubishi MELSERVO-J4
python upload_manual.py manuals/FX5U매뉴얼.pdf Mitsubishi MELSEC-FX5U
python upload_manual.py manuals/SV-iG5A매뉴얼.pdf LS SV-iG5A
python upload_manual.py manuals/오토닉스인코더.pdf Autonics E-Series
```

## 주의: 투입 대상 DB

`upload_manual.py`는 `.env`의 `DATABASE_URL`이 가리키는 DB에 **그대로 쓴다.**
현재 값이 프로덕션 MariaDB이면 결과가 곧바로 프로덕션에 반영되고, 롤백은
수동 DELETE뿐이다. 처음 넣는 매뉴얼은 SQLite로 한 번 돌려 추출 품질을 본 뒤
프로덕션에 넣는 것을 권장한다.

환경변수가 `.env`보다 우선하므로, 파일을 고치지 않고 한 번만 우회할 수 있다.

```powershell
# 드라이런 (PowerShell — 임시로 로컬 SQLite에 투입)
$env:DATABASE_URL = "sqlite+aiosqlite:///./dryrun.db"
python upload_manual.py manuals/파일.pdf LS LSLV-S100
Remove-Item Env:DATABASE_URL     # 끝나면 원복 (안 지우면 이후 명령도 SQLite를 본다)
```

원복을 잊으면 이어지는 조회/검증 스크립트가 전부 빈 SQLite를 보게 되므로,
드라이런이 끝나면 `Remove-Item Env:DATABASE_URL`을 반드시 실행한다.

## 제조사/시리즈 명칭 규칙

| 제조사 | 시리즈 | 예시 모델 |
|---|---|---|
| Mitsubishi | MELSERVO-J4 | MR-J4-10A ~ MR-J4-700A |
| Mitsubishi | MELSERVO-J2S | MR-J2S-10A ~ MR-J2S-700A |
| Mitsubishi | MELSEC-FX5U | FX5U-32MT/ES |
| Mitsubishi | FR-E840 | FR-E840 인버터 |
| LS | SV-iG5A | SV008iG5A-4 |
| LS | SV-iS7 | SV-iS7 인버터 |
| LS | LSLV-S100 | LSLV0022S100-2 (알람 36 / 제품 32 / 치수 O) |
| LS | LSLV-G100 | LSLV0022G100-2 (알람 35 / 제품 13 / 치수 X — 위 참조) |
| LS | LSLV-H100 | LSLV0022H100-2 (알람 49 / 제품 35 / 치수 O) |
| LS | XGB | XBM-DR16S |
| Autonics | E-Series | E40H8, E50S8 |
| Proface | GP4000 | GP-4301T |
