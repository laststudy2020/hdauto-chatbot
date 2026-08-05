"""LS 신형 인버터(S100/G100/H100) 매뉴얼 전용 파서 — 결정적 추출.

범용 `pdf_processor`를 이 매뉴얼들에 쓸 수 없어서 별도로 둔다. 이유:

1. 페이지 선별. 범용 쪽은 '고장'·'trip' 같은 키워드가 걸린 페이지를 **앞에서부터**
   N개 담는데, 이 매뉴얼들은 안전주의사항에도 '고장'이 나와 141~240페이지가
   매칭된다. 결과적으로 앞부분 설치 페이지만 담기고 정작 고장표(S100 p.429~)는
   한 장도 안 들어온다.
2. 텍스트 레이어. G100은 pdfplumber가 표 본문을 거의 못 읽는다(p.291이 218자
   /이미지 174개). PyMuPDF(fitz)는 같은 페이지에서 949자를 읽는다. 그래서 본문은
   fitz로 읽고, 괘선 좌표가 필요한 치수표에서만 pdfplumber를 쓴다.
3. LLM을 안 거친다. 코드·치수·중량은 지어내면 안 되는 값이라 좌표 정렬로만 뽑고,
   열 개수가 안 맞는 행은 통째로 버린다(값이 밀려 다른 모델에 붙는 사고 방지).

호출부는 `ingest_ls_manual.py`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 고장표 '고장 상태' 열에 오는 값. 항목마다 정확히 한 번 나와 항목 경계 앵커가 된다.
TRIP_STATES = {"latch", "level", "hardware", "warning"}

_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9\-]{1,7}$")
# LCD 표시 명칭은 대문자로 시작하는 두 글자 이상의 영문 표기다.
_NAME_OK = re.compile(r"^[A-Z][A-Za-z0-9][A-Za-z0-9 .%/()+\-]*$")
_MODEL_CODE_RE = re.compile(r"^\d{4}$")
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_HANGUL_RE = re.compile(r"[가-힣]")


@dataclass
class ParsedAlarm:
    code: str
    name: str
    state: str
    description: str
    page: int
    actions: list[tuple[str, str]] = field(default_factory=list)  # (진단, 조치)

    def solution_text(self) -> str:
        return " / ".join(f"{d} → {a}" if a else d for d, a in self.actions)


@dataclass
class ParsedModel:
    model_name: str
    capacity_kw: float | None = None
    voltage_class: str | None = None
    rated_current_a: float | None = None
    weight_kg: float | None = None
    dimension_w: float | None = None
    dimension_h: float | None = None
    dimension_d: float | None = None
    spec_page: int | None = None
    dim_page: int | None = None


@dataclass
class ParsedManual:
    series: str
    alarms: list[ParsedAlarm] = field(default_factory=list)
    models: list[ParsedModel] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ────────────────────────── 공통 유틸 ──────────────────────────

def _despace(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _camel_split(name: str) -> str:
    """G100은 텍스트 레이어가 깨져 'Out Phas'/'e Open'처럼 잘린다.

    공백을 지운 뒤 카멜 경계로 다시 끊으면 세 시리즈가 같은 표기로 모인다.
    ('OutPhaseOpen' → 'Out Phase Open', 'NTCOpen' → 'NTC Open')
    """
    s = _despace(name)
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    return s.strip()


def _rows(words: list[dict], tol: float = 3.0) -> list[tuple[float, list[dict]]]:
    """y좌표가 같은 단어들을 한 행으로 묶는다."""
    groups: list[list] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if groups and abs(w["top"] - groups[-1][0]) <= tol:
            groups[-1][1].append(w)
        else:
            groups.append([w["top"], [w]])
    return [(top, sorted(ws, key=lambda x: x["x0"])) for top, ws in groups]


def _rules(page) -> tuple[list[float], list[float]]:
    """페이지의 표 괘선(가로선 y, 세로선 x)을 뽑는다.

    표를 텍스트 줄 단위로 읽으면 안 된다. 이 매뉴얼들은 셀 안에서 명칭이
    세로 가운데 정렬이라, 설명 첫 줄이 명칭보다 **윗줄**에 온다. 줄 순서만
    믿으면 그 줄이 앞 항목 설명으로 붙는다. 괘선으로 셀을 갈라야 맞는다.
    """
    hspan: dict[float, list[float]] = {}
    vspan: dict[float, list[float]] = {}

    def add(store, key, a, b):
        s = store.setdefault(key, [a, b])
        s[0] = min(s[0], a)
        s[1] = max(s[1], b)

    for d in page.get_drawings():
        for it in d.get("items", ()):
            if it[0] == "l":
                p1, p2 = it[1], it[2]
                if abs(p1.y - p2.y) < 0.8:
                    add(hspan, round((p1.y + p2.y) / 2), *sorted((p1.x, p2.x)))
                elif abs(p1.x - p2.x) < 0.8:
                    add(vspan, round((p1.x + p2.x) / 2), *sorted((p1.y, p2.y)))
            elif it[0] == "re":
                r = it[1]
                if r.height < 2.0:
                    add(hspan, round((r.y0 + r.y1) / 2), r.x0, r.x1)
                elif r.width < 2.0:
                    add(vspan, round((r.x0 + r.x1) / 2), r.y0, r.y1)

    def merge(vals: list[float], gap: float) -> list[float]:
        out: list[float] = []
        for v in sorted(vals):
            if not out or v - out[-1] > gap:
                out.append(v)
        return out

    hs = merge([k for k, (a, b) in hspan.items() if b - a > 150], 2.0)
    vs = merge([k for k, (a, b) in vspan.items() if b - a > 20], 3.0)
    return hs, vs


def _fitz_pages(pdf_bytes: bytes) -> list[dict]:
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = []
    try:
        for page in doc:
            ws = []
            for x0, y0, x1, y1, txt, *_ in page.get_text("words"):
                t = txt.strip()
                if t:
                    ws.append({"x0": x0, "x1": x1, "top": y0, "text": t})
            hs, vs = _rules(page) if ws else ([], [])
            out.append({"words": ws, "hlines": hs, "vlines": vs})
    finally:
        doc.close()
    return out


def _flat(words: list[dict]) -> str:
    return _despace(" ".join(w["text"] for w in words))


def _bands(hlines: list[float]) -> list[tuple[float, float]]:
    return list(zip(hlines, hlines[1:]))


def _band_words(words: list[dict], top: float, bottom: float) -> list[dict]:
    """표 한 칸 범위의 단어를 읽는 순서(줄→왼쪽부터)로 정렬해 돌려준다.

    y를 그대로 쓰면 안 된다. 숫자만 다른 폰트로 찍히는 곳이 있어 같은 줄인데도
    baseline이 1pt쯤 어긋나고, 그러면 '정격 전류의 200% 이상일 때'가
    '정격 전류의 이상일 200% 때'로 뒤섞인다.
    """
    return sorted((w for w in words if top <= w["top"] < bottom),
                  key=lambda w: (round(w["top"] / 4), w["x0"]))


def _region_of(x: float, vlines: list[float]) -> int:
    idx = 0
    for v in vlines:
        if x + 2 >= v:
            idx += 1
        else:
            break
    return idx


# ────────────────────────── 고장(트립) 코드 ──────────────────────────

_HEADER_HINTS = ("LCD", "명칭", "키패드")


def _is_header_band(flat: str) -> bool:
    return "내용" in flat and any(h in flat for h in _HEADER_HINTS)


def _trip_roles(band_words: list[dict], vlines: list[float]) -> dict[int, str]:
    """헤더 라벨이 놓인 열 구역에 역할을 붙인다.

    헤더 표기가 시리즈마다 다르다.
      S100 '키패드 표시 | LCD 표시 | 고장 상태 | 내용'
      G100 '키패드 표시 | 명칭     | 고장 상태 | 내용'
      H100 'LCD 표시    | 고장 상태 | 내용'      (키패드 열 자체가 없다)
    """
    roles: dict[int, str] = {}
    for w in band_words:
        t = w["text"]
        r = _region_of((w["x0"] + w["x1"]) / 2, vlines)
        if t.startswith("키패드"):
            roles.setdefault(r, "code")
        elif t == "LCD" or t.startswith("명칭"):
            roles.setdefault(r, "name")
        elif t.startswith("고장"):
            roles.setdefault(r, "state")
        elif t == "내용":
            roles.setdefault(r, "desc")
    return roles


def _is_trip_page(flat: str) -> bool:
    return "문제해결" in flat and _is_header_band(flat)


def _parse_trip_page(page: dict, pageno: int) -> list[ParsedAlarm]:
    words, hlines, vlines = page["words"], page["hlines"], page["vlines"]
    if len(hlines) < 2 or len(vlines) < 2:
        return []

    entries: list[ParsedAlarm] = []
    cur: dict | None = None
    roles: dict[int, str] = {}

    def flush() -> None:
        nonlocal cur
        if cur:
            name = _camel_split(" ".join(cur["name"])).rstrip("*").strip()
            # 한 칸에 코드가 둘 실리는 행이 있다(sfa/sfb → Safety A(B) Err).
            # 붙여 쓰면 'SFBSFA' 같은 없는 코드가 되므로 슬래시로 나눠 둔다.
            code = "/".join(t.upper() for t in " ".join(cur["code"]).split())
            if name or code:
                entries.append(ParsedAlarm(
                    code=code, name=name, state=cur["state"],
                    description=re.sub(r"\s+", " ", " ".join(cur["desc"])).strip(),
                    page=pageno,
                ))
        cur = None

    for top, bottom in _bands(hlines):
        bw = _band_words(words, top, bottom)
        if not bw:
            continue
        flat = _despace(" ".join(w["text"] for w in bw))
        if _is_header_band(flat):
            flush()
            roles = _trip_roles(bw, vlines)
            continue
        if not roles or flat.isdigit() or flat.startswith("문제해결"):
            continue

        cells: dict[str, list[str]] = {}
        for w in bw:
            r = roles.get(_region_of((w["x0"] + w["x1"]) / 2, vlines))
            if r:
                cells.setdefault(r, []).append(w["text"])

        code = " ".join(t for t in cells.get("code", []) if _CODE_RE.match(t))
        name = " ".join(t for t in cells.get("name", []) if not _HANGUL_RE.search(t))
        state = " ".join(cells.get("state", [])).strip()
        desc = " ".join(cells.get("desc", [])).strip()
        if state and _despace(state).lower() not in TRIP_STATES:
            # '고장 상태' 열에 엉뚱한 게 들어오면 내용 쪽으로 흘려보낸다.
            desc = f"{state} {desc}".strip()
            state = ""

        if code or name:
            flush()
            cur = {"code": [], "name": [], "state": "", "desc": []}
        if not cur:
            continue
        if code:
            cur["code"].append(code)
        if name:
            cur["name"].append(name)
        if state:
            cur["state"] = state
        if desc:
            cur["desc"].append(desc)

    flush()
    return entries


def parse_trips(pages: list[dict]) -> tuple[list[ParsedAlarm], list[str]]:
    notes: list[str] = []
    alarms: list[ParsedAlarm] = []
    for pageno, page in enumerate(pages, 1):
        if not page["words"] or not _is_trip_page(_flat(page["words"])):
            continue
        page_alarms = _parse_trip_page(page, pageno)
        if not page_alarms:
            notes.append(f"p.{pageno}: 고장표로 보이는데 항목을 못 뽑음")
            continue

        # 명칭 자리가 'Latch'거나 한 글자짜리 파편이라는 건 텍스트 객체의 y좌표가
        # 실제 표 행과 어긋났다는 뜻이다(G100이 그렇다). 이 상태로 넣으면
        # 코드와 설명이 서로 다른 항목끼리 묶여 조용히 틀린 답을 하게 된다.
        bad = sum(1 for a in page_alarms
                  if not _NAME_OK.match(a.name) or _despace(a.name).lower() in TRIP_STATES)
        if bad * 5 >= len(page_alarms):
            notes.append(
                f"p.{pageno}: 명칭 열이 어긋남({bad}/{len(page_alarms)}) → 페이지 통째 폐기"
            )
            continue
        alarms.extend(page_alarms)

    # 키패드 코드가 없는 시리즈(G100/H100)는 LCD 명칭이 곧 표시값이다.
    for a in alarms:
        if not a.code:
            a.code = a.name[:20]
    return alarms, notes


# ────────────────────────── 조치 사항 표 ──────────────────────────

def _action_roles(band_words: list[dict], vlines: list[float]) -> dict[int, str]:
    roles: dict[int, str] = {}
    for w in band_words:
        t = w["text"]
        r = _region_of((w["x0"] + w["x1"]) / 2, vlines)
        if t.startswith("항목"):
            roles.setdefault(r, "item")
        elif t.startswith("진단"):
            roles.setdefault(r, "diag")
        elif t.startswith("조치"):
            roles.setdefault(r, "act")
    return roles


def parse_actions(pages: list[dict]) -> dict[str, list[tuple[str, str]]]:
    """'트립 발생 시 조치 사항' 표 → {LCD 명칭: [(진단, 조치), ...]}.

    같은 장 뒤쪽에는 트립과 무관한 일반 문제 해결표('모터가 회전하지 않습니다'
    등)가 이어진다. 호출부에서 실제 고장 명칭과 대조해 걸러 쓴다.
    """
    out: dict[str, list[tuple[str, str]]] = {}
    for page in pages:
        words, hlines, vlines = page["words"], page["hlines"], page["vlines"]
        if not words or len(hlines) < 2 or len(vlines) < 2:
            continue
        flat = _flat(words)
        if "조치사항" not in flat or "진단" not in flat:
            continue

        roles: dict[int, str] = {}
        cur = ""
        for top, bottom in _bands(hlines):
            bw = _band_words(words, top, bottom)
            if not bw:
                continue
            bflat = _despace(" ".join(w["text"] for w in bw))
            if "항목" in bflat and "진단" in bflat and "조치" in bflat:
                roles = _action_roles(bw, vlines)
                continue
            if not roles:
                continue

            cells: dict[str, list[str]] = {}
            for w in bw:
                r = roles.get(_region_of((w["x0"] + w["x1"]) / 2, vlines))
                if r:
                    cells.setdefault(r, []).append(w["text"])
            item = " ".join(cells.get("item", [])).strip()
            diag = " ".join(cells.get("diag", [])).strip()
            act = " ".join(cells.get("act", [])).strip()

            if item:
                cur = item
            if not cur:
                continue
            if diag:
                out.setdefault(cur, []).append((diag, act))
            elif act and out.get(cur):
                # 조치 칸만 넘어온 줄은 바로 앞 조치의 이어짐이다.
                d, a = out[cur][-1]
                out[cur][-1] = (d, f"{a} {act}".strip())
    return out


# ────────────────────────── 정격(사양) 표 ──────────────────────────

def _voltage_class(words: list[dict]) -> str | None:
    m = re.search(r"(단상|3상)(200|400)V", _flat(words))
    return f"{m.group(1)} {m.group(2)}V" if m else None


def _column_values(ws: list[dict], columns: list[tuple[str, float]],
                   tol: float) -> dict[str, float]:
    out: dict[str, float] = {}
    for w in ws:
        if not _NUM_RE.match(w["text"]):
            continue
        cx = (w["x0"] + w["x1"]) / 2
        best, best_d = None, tol
        for code, colx in columns:
            d = abs(cx - colx)
            if d < best_d:
                best, best_d = code, d
        if best and best not in out:
            out[best] = float(w["text"])
    # 열 수와 값 수가 다르면 값이 밀린 것으로 보고 통째로 버린다.
    return out if len(out) == len(columns) else {}


def parse_ratings(pages: list[dict], series: str
                  ) -> tuple[dict[str, ParsedModel], list[str]]:
    """'입력 및 출력 규격' 표에서 형명·용량·정격전류·중량을 뽑는다."""
    models: dict[str, ParsedModel] = {}
    notes: list[str] = []

    for pageno, page in enumerate(pages, 1):
        words = page["words"]
        if not words:
            continue
        flat = _flat(words)
        if "모델명" not in flat or series not in flat:
            continue

        rows = _rows(words)
        head_i = next((i for i, (_t, ws) in enumerate(rows)
                       if any(w["text"].startswith("모델명") for w in ws)), None)
        if head_i is None:
            continue

        # 모델명 셀이 '모델명L / SLV / G100 / – 2'처럼 여러 줄로 접히는 경우가 있다.
        best = max(rows[head_i:head_i + 4],
                   key=lambda r: sum(1 for w in r[1] if _MODEL_CODE_RE.match(w["text"])))
        codes = [w for w in best[1] if _MODEL_CODE_RE.match(w["text"])]
        if len(codes) < 2:
            continue  # 정격표가 아니라 형명이 스쳐 지나간 페이지

        vclass = _voltage_class(words)
        head_text = " ".join(w["text"] for _t, ws in rows[head_i:head_i + 4] for w in ws)
        vm = (re.search(r"[–\-]\s*(\d)", head_text.split(series, 1)[1])
              if series in head_text else None)
        # 형명 끝자리는 전압등급을 그대로 따른다(단상200V=1, 3상200V=2, 3상400V=4).
        # G100은 모델명 셀이 'L SLV / G100 / – 2'로 쪼개져 정규식이 빗나가므로
        # 표 제목의 전압 표기에서 되짚는다.
        volt_digit = vm.group(1) if vm else {
            "단상 200V": "1", "3상 200V": "2", "3상 400V": "4",
        }.get(vclass or "")
        if not volt_digit:
            notes.append(f"p.{pageno}: 전압등급 자리(-1/-2/-4)를 못 정해 건너뜀")
            continue

        columns = [(w["text"], (w["x0"] + w["x1"]) / 2) for w in codes]
        centers = sorted(c for _n, c in columns)
        pitch = min(b - a for a, b in zip(centers, centers[1:]))
        tol = max(pitch * 0.45, 6.0)

        page_models: dict[str, ParsedModel] = {}
        for code, _cx in columns:
            name = f"LSLV{code}{series}-{volt_digit}"
            m = models.setdefault(name, ParsedModel(model_name=name))
            m.voltage_class = vclass
            m.spec_page = pageno
            page_models[code] = m

        # 라벨이 값과 다른 줄에 있는 표가 많다('정격 전류(A)' 줄 아래 '중부하 2.5 ...').
        pending = ""
        done: set[str] = set()
        for _t, ws in rows[head_i:]:
            labels = _despace(" ".join(w["text"] for w in ws
                                       if not _NUM_RE.match(w["text"])))
            vals = _column_values(ws, columns, tol)
            if not vals:
                if labels:
                    pending = labels
                continue
            target = None
            for src in (labels, pending):
                if "kW" in src and "kW" not in done:
                    target = "kW"
                elif "정격전류" in src and "정격전류" not in done:
                    target = "정격전류"
                elif "중량" in src and "중량" not in done:
                    target = "중량"
                if target:
                    break
            pending = ""
            if not target:
                continue
            done.add(target)
            for code, v in vals.items():
                m = page_models[code]
                if target == "kW":
                    # 형명 4자리는 kW×10 표기다(0008만 0.75). 크게 어긋나면 밀린 값이다.
                    if abs(v * 10 - int(code)) > 6:
                        continue
                    m.capacity_kw = v
                elif target == "정격전류":
                    m.rated_current_a = v
                else:
                    m.weight_kg = v

    return models, notes


# ────────────────────────── 외형 치수 표 ──────────────────────────

def parse_dimensions(pages: list[dict], series: str
                     ) -> tuple[dict[str, tuple[float, float, float, int]], list[str]]:
    """형명 → (W1, H1, D1, 페이지). 확신 없는 행은 통째로 버린다."""
    out: dict[str, tuple[float, float, float, int]] = {}
    notes: list[str] = []
    model_re = re.compile(rf"(\d{{4}}{series}-\d)")

    for pageno, page in enumerate(pages, 1):
        words = page["words"]
        if not words:
            continue
        flat = _flat(words)
        if not all(k in flat for k in ("제품", "W1", "H1", "D1")):
            continue

        header = None
        for _t, ws in _rows(words):
            texts = [w["text"] for w in ws]
            if "W1" in texts and "H1" in texts and "D1" in texts:
                header = ws
                break
        if header is None:
            continue
        colx = {w["text"]: (w["x0"] + w["x1"]) / 2 for w in header
                if w["text"] in ("W1", "H1", "D1")}
        if len(colx) < 3:
            continue

        bands = _bands(page["hlines"])
        if not bands:
            notes.append(f"p.{pageno}: 치수표 괘선을 못 찾아 건너뜀")
            continue

        hit = 0
        for top, bottom in bands:
            band = _band_words(words, top, bottom)
            if not band:
                continue
            names = model_re.findall(_despace(" ".join(w["text"] for w in band)))
            if not names:
                continue
            vals: dict[str, float] = {}
            for key, cx in colx.items():
                best, best_d = None, 12.0
                for w in band:
                    t = w["text"]
                    # (2.68)처럼 괄호로 병기된 인치 값은 건너뛴다.
                    if t.startswith("(") or not _NUM_RE.match(t):
                        continue
                    d = abs((w["x0"] + w["x1"]) / 2 - cx)
                    if d < best_d:
                        best, best_d = float(t), d
                if best is not None:
                    vals[key] = best
            if len(vals) < 3:
                continue
            hit += 1
            for n in names:
                out.setdefault(f"LSLV{n}", (vals["W1"], vals["H1"], vals["D1"], pageno))
        if hit == 0:
            notes.append(f"p.{pageno}: 치수 행을 하나도 못 읽음")
    return out, notes


# ────────────────────────── 진입점 ──────────────────────────

def parse_ls_manual(pdf_bytes: bytes, series: str) -> ParsedManual:
    """series는 'S100'/'G100'/'H100'."""
    pages = _fitz_pages(pdf_bytes)
    result = ParsedManual(series=series)

    result.alarms, n1 = parse_trips(pages)
    result.notes += n1

    actions = parse_actions(pages)
    for a in result.alarms:
        a.actions = actions.get(a.name, [])
    linked = sum(1 for a in result.alarms if a.actions)
    result.notes.append(f"조치 사항: 고장 {len(result.alarms)}건 중 {linked}건 연결")

    models, n2 = parse_ratings(pages, series)
    result.notes += n2

    dims, n3 = parse_dimensions(pages, series)
    result.notes += n3

    for name, (w, h, d, pg) in dims.items():
        m = models.setdefault(name, ParsedModel(model_name=name))
        m.dimension_w, m.dimension_h, m.dimension_d, m.dim_page = w, h, d, pg

    result.models = sorted(models.values(), key=lambda m: m.model_name)
    return result
