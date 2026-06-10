# HD AUTO 부품 도우미 챗봇

현대자동화 현대기전사 스마트스토어용 FA 산업 부품 챗봇

## 기능
1. 단종품 대체품 조회 + 유사 제품군 표시
2. 규격/사이즈/동작 사양 상세 조회
3. 고장 알람 → 매뉴얼 기반 진단 안내
4. 현대자동화 위치 안내 + 네비게이션
5. 재고 부족 관리자 알림
6. 경쟁사 단가 비교 리포트
7. 네이버 톡톡 연동

## 기술 스택
- **Backend**: Python 3.11+ / FastAPI
- **AI**: 네이버 CLOVA Studio HyperCLOVA X
- **DB**: SQLite(개발) → PostgreSQL(운영) + Qdrant(벡터DB)
- **배포**: Railway.app 또는 AWS Lightsail

## 빠른 시작

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. 환경변수 설정
cp .env.example .env
# .env 파일에서 CLOVA API 키 입력

# 3. 서버 실행
uvicorn app.main:app --reload --port 8000

# 4. API 문서 확인
# http://localhost:8000/docs
```

## 데이터 입력

```bash
# 샘플 제품 CSV 임포트
curl -X POST http://localhost:8000/api/products/import/csv \
  -F "file=@data/sample_products.csv"

# DB 통계 확인
curl http://localhost:8000/api/products/stats
```

## 챗봇 테스트

```bash
# 단종 대체품 질문
curl -X POST http://localhost:8000/api/chat/ \
  -H "Content-Type: application/json" \
  -d '{"message": "FX3U-32MT 단종됐는데 대체품 뭐예요?"}'

# 규격 조회
curl -X POST http://localhost:8000/api/chat/ \
  -H "Content-Type: application/json" \
  -d '{"message": "FX5U-32MT 사이즈 알려줘"}'

# 위치 안내
curl -X POST http://localhost:8000/api/chat/ \
  -H "Content-Type: application/json" \
  -d '{"message": "현대자동화 위치가 어디예요?"}'
```

## 프로젝트 구조

```
hdauto-chatbot/
├── app/
│   ├── main.py              # FastAPI 서버 시작점
│   ├── config.py             # 환경변수 설정
│   ├── api/
│   │   ├── chatbot.py        # 챗봇 엔드포인트 (의도분류→서비스 라우팅)
│   │   └── products.py       # 제품 CRUD + CSV 임포트
│   ├── core/
│   │   ├── clova.py          # CLOVA Studio API 클라이언트
│   │   └── intent.py         # 의도 분류기 (7가지 기능 라우팅)
│   ├── db/
│   │   ├── database.py       # DB 연결
│   │   └── models.py         # 7개 테이블 정의
│   ├── services/
│   │   ├── replacement.py    # 단종 대체품 검색
│   │   ├── specs.py          # 규격/스펙 조회
│   │   └── location.py       # 위치 안내
│   └── models/
│       └── schemas.py        # Pydantic 스키마
├── data/
│   ├── sample_products.csv   # 샘플 제품 데이터
│   └── sample_replacements.csv
├── .env.example
└── requirements.txt
```
