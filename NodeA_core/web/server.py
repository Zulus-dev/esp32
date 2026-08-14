# web/server.py — single-flight HTTP + mempool TX slabs
#
# RAM RULES:
# 1. One request in flight (_busy). backlog=1.
# 2. TX via mempool web_tx — no per-chunk bytearray().
# 3. After each client: close writer + gc.collect() x2.
# 4. free < CRITICAL → 503.
# 5. API bodies: binary octet-stream except wifi_settings JSON.
#

import asyncio
import gc
import uos

from web.api import FileAPI

_MIME = {
    "html": b"text/html",
    "css": b"text/css",
    "js": b"application/javascript",
    "json": b"application/json",
    "ico": b"image/x-icon",
}


class AsyncServer:
    def __init__(self, port=80, core=None):
        self.port = port
        self.core = core
        self.is_running = False
        self._server = None
        self.api = FileAPI(self.core)
        self._busy = False
        self._tx = None

    async def start(self):
        if self.is_running:
            return
        from lib import mempool
        mempool.ensure_web_slabs()
        self._tx = mempool.acquire("web_tx", owner="web")
        if self._tx is None:
            self._tx = mempool.register("web_tx", 1024, sticky=True)
            mempool.acquire("web_tx", owner="web")
        if self.api is None:
            self.api = FileAPI(self.core)
        self._server = await asyncio.start_server(
            self.handle_client, "0.0.0.0", self.port, backlog=1
        )
        self.is_running = True
        print("[WEB] on :%d free=%d" % (self.port, gc.mem_free()))

    async def stop(self):
        if self._server:
            try:
                self._server.close()
                await self._server.wait_closed()
            except Exception:
                pass
        self._server = None
        self.api = None
        self.is_running = False
        self._busy = False
        try:
            from lib import mempool
            mempool.release("web_tx", drop=True)
            mempool.release("web_list", drop=True)
            mempool.release("web_file", drop=True)
        except Exception:
            pass
        self._tx = None
        gc.collect()
        gc.collect()

    async def handle_client(self, reader, writer):
        waited = 0
        while self._busy:
            await asyncio.sleep_ms(15)
            waited += 15
            if waited > 3000:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
                return
        try:
            from lib import mempool
            if not mempool.guard_or_gc():
                try:
                    writer.write(
                        b"HTTP/1.1 503 Service Unavailable\r\n"
                        b"Connection: close\r\nRetry-After: 2\r\n\r\n"
                    )
                    await writer.drain()
                except Exception:
                    pass
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
                return
        except Exception:
            pass
        self._busy = True
        try:
            line = await asyncio.wait_for(reader.readline(), 2)
            if not line:
                return
            try:
                req = line.decode().strip().split()
            except Exception:
                return
            if len(req) < 2:
                return
            method, url = req[0], req[1]

            headers = {}
            while True:
                header = await asyncio.wait_for(reader.readline(), 1)
                if not header or header == b"\r\n":
                    break
                hl = header.lower()
                if hl.startswith(b"content-length:"):
                    try:
                        headers["content-length"] = header.split(b":", 1)[1].strip().decode()
                    except Exception:
                        pass

            if url.startswith("/api/"):
                await self.api.handle(method, url, reader, writer, headers)
            else:
                await self._serve_static(url, writer)
        except Exception as exp:
            print("[WEB]", exp)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                try:
                    await writer.aclose()
                except Exception:
                    pass
            self._busy = False
            gc.collect()
            gc.collect()

    async def _serve_static(self, url, writer):
        url = url.split("?", 1)[0]
        if url == "/":
            url = "/index.html"
        if ".." in url or url.startswith("//"):
            writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return

        path = "/static" + url
        try:
            size = uos.stat(path)[6]
            if size > 64 * 1024:
                writer.write(b"HTTP/1.1 413 Payload Too Large\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            ext = url.rsplit(".", 1)[-1]
            mime = _MIME.get(ext, b"text/plain")
            hdr = (
                b"HTTP/1.1 200 OK\r\nContent-Type: " + mime
                + b"\r\nContent-Length: " + str(size).encode()
                + b"\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n"
            )
            writer.write(hdr)
            await writer.drain()

            buf = self._tx
            if buf is None:
                buf = bytearray(512)
            with open(path, "rb") as f:
                while True:
                    n = f.readinto(buf)
                    if not n:
                        break
                    writer.write(memoryview(buf)[:n])
                    await writer.drain()
                    await asyncio.sleep_ms(0)
        except Exception:
            try:
                writer.write(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")
                await writer.drain()
            except Exception:
                pass
