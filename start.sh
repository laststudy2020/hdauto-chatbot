#!/bin/bash
set -e

mkdir -p /tmp/tailscale

echo "[start.sh] tailscaled(userspace 모드) 기동 중..."
./tailscaled \
  --tun=userspace-networking \
  --socks5-server=127.0.0.1:1055 \
  --state=/tmp/tailscale/tailscaled.state \
  --socket=/tmp/tailscale/tailscaled.sock &

sleep 3

echo "[start.sh] tailnet에 로그인 중..."
./tailscale --socket=/tmp/tailscale/tailscaled.sock up \
  --authkey="${TS_AUTHKEY}" \
  --hostname=hdauto-render \
  --accept-dns=false

echo "[start.sh] SOCKS5 -> NAS DB 포워더 기동 중..."
python tailscale_proxy.py &

sleep 2

echo "[start.sh] FastAPI 앱 기동..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
