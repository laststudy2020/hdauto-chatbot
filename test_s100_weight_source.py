"""S100 중량표 원본 대조 — DB의 중복 중량이 PDF 실제값인지 파서 오정렬인지 판별.

'중량'이 들어간 페이지를 찾아 그 페이지 텍스트를 그대로 찍는다. 사람이 눈으로
표를 보고 DB 값과 맞춰 보기 위한 것이라 판정 로직은 넣지 않는다.
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

import fitz  # PyMuPDF

PDF = "manuals/LSLV-S100.pdf"
NEEDLES = ("0022",)


def main() -> None:
    doc = fitz.open(PDF)
    hits = []
    for i in range(doc.page_count):
        text = doc[i].get_text()
        if "중량" in text and any(n in text for n in NEEDLES):
            hits.append(i)
    print(f"'중량'+용량코드가 함께 있는 페이지: {[p + 1 for p in hits]}")

    for p in hits:
        text = doc[p].get_text()
        i, j = text.find("모델명"), text.find("중량")
        print(f"\n{'=' * 74}\n--- p.{p + 1} ---\n{'=' * 74}")
        print("[모델 헤더] " + " ".join(text[i:i + 90].split()))
        print("[중량 구간] " + " ".join(text[j:j + 300].split()))


if __name__ == "__main__":
    main()
