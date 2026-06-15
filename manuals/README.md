# 매뉴얼 PDF 폴더

이 폴더에 제조사 매뉴얼 PDF를 넣고 upload_manual.py로 처리하세요.

## 사용법

1. 이 폴더에 PDF 파일 복사
2. 터미널에서 실행:

```
python upload_manual.py manuals/MR-J4매뉴얼.pdf Mitsubishi MELSERVO-J4
python upload_manual.py manuals/FX5U매뉴얼.pdf Mitsubishi MELSEC-FX5U
python upload_manual.py manuals/SV-iG5A매뉴얼.pdf LS SV-iG5A
python upload_manual.py manuals/오토닉스인코더.pdf Autonics E-Series
```

## 제조사/시리즈 명칭 규칙

| 제조사 | 시리즈 | 예시 모델 |
|---|---|---|
| Mitsubishi | MELSERVO-J4 | MR-J4-10A ~ MR-J4-700A |
| Mitsubishi | MELSERVO-J2S | MR-J2S-10A ~ MR-J2S-700A |
| Mitsubishi | MELSEC-FX5U | FX5U-32MT/ES |
| Mitsubishi | FR-E840 | FR-E840 인버터 |
| LS | SV-iG5A | SV008iG5A-4 |
| LS | SV-iS7 | SV-iS7 인버터 |
| LS | XGB | XBM-DR16S |
| Autonics | E-Series | E40H8, E50S8 |
| Proface | GP4000 | GP-4301T |
