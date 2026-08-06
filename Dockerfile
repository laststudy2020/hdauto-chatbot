# 시놀로지 NAS(Container Manager) 배포용 이미지.
#
# Render 배포와 결정적으로 다른 점: tailscaled와 tailscale_proxy.py가 없다.
# Render는 컨테이너 밖에 있어서 NAS의 MariaDB에 닿으려면 tailnet을 타야 했지만,
# 이 이미지는 그 MariaDB와 같은 기계에서 돈다. docker-compose.yml이
# network_mode: host로 띄우므로 앱은 127.0.0.1:3306으로 바로 붙는다.

# ── 빌드 단계 ───────────────────────────────────────────────
# asyncmy는 휠이 없으면 Cython 컴파일이 필요하다. 컴파일러를 이 단계에만 두고
# 최종 이미지에는 venv만 넘겨서 런타임 이미지를 얇게 유지한다.
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── 런타임 단계 ─────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    TZ=Asia/Seoul

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app ./app

# 앱은 8000(>1024)에 바인딩하므로 root일 이유가 없다.
RUN useradd -r -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# PORT는 Render 호환을 위해 남겨둔다(NAS에서는 기본 8000).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
