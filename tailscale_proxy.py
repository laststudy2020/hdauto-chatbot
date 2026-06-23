"""
Tailscale SOCKS5 프록시(userspace 모드, 127.0.0.1:1055)를 거쳐
NAS의 Tailscale IP:3306으로 평문 TCP를 그대로 중계하는 로컬 포워더.

Render 컨테이너는 TUN 디바이스 권한이 없어서 tailscaled를
--tun=userspace-networking 모드로만 띄울 수 있다. 이 모드는
SOCKS5 프록시만 노출하는데, asyncmy/pymysql은 SOCKS5를 직접
지원하지 않는다. 그래서 앱은 그냥 127.0.0.1:13306(이 포워더)에
연결하고, 이 포워더가 SOCKS5를 거쳐 실제 NAS DB까지 이어준다.

앱 입장에서는 평범한 로컬 MariaDB에 연결하는 것처럼 보인다.
"""

import asyncio
import os

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = int(os.getenv("TS_PROXY_LISTEN_PORT", "13306"))

SOCKS_HOST = "127.0.0.1"
SOCKS_PORT = int(os.getenv("TS_SOCKS5_PORT", "1055"))

DEST_HOST = os.getenv("NAS_TAILSCALE_IP", "")
DEST_PORT = int(os.getenv("NAS_DB_PORT", "3306"))

from python_socks.async_.asyncio import Proxy  # noqa: E402


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    proxy = Proxy.from_url(f"socks5://{SOCKS_HOST}:{SOCKS_PORT}")
    try:
        sock = await proxy.connect(dest_host=DEST_HOST, dest_port=DEST_PORT)
    except Exception as e:
        print(f"[tailscale_proxy] NAS 연결 실패 ({DEST_HOST}:{DEST_PORT}): {e}")
        writer.close()
        return

    remote_reader, remote_writer = await asyncio.open_connection(sock=sock)

    await asyncio.gather(
        _pipe(reader, remote_writer),
        _pipe(remote_reader, writer),
    )


async def main():
    if not DEST_HOST:
        raise SystemExit("[tailscale_proxy] NAS_TAILSCALE_IP 환경변수가 설정되지 않았습니다.")

    server = await asyncio.start_server(handle_client, LISTEN_HOST, LISTEN_PORT)
    print(
        f"[tailscale_proxy] {LISTEN_HOST}:{LISTEN_PORT} -> "
        f"(SOCKS5 {SOCKS_HOST}:{SOCKS_PORT}) -> {DEST_HOST}:{DEST_PORT}"
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
