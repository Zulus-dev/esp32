# web/server.py - tiny static-file server plus JSON API
import asyncio

import gc
import uos
from web.api import FileAPI


class AsyncServer:
    def __init__(self, port=80, core=None):
        self.port = port
        self.core = core
        self.is_running = False
        self._server = None
        self.api = FileAPI(self.core)

    async def start(self):
        if self.is_running:
            return
        if self.api is None:
            self.api = FileAPI(self.core)
        self._server = await asyncio.start_server(self.handle_client, "0.0.0.0", self.port, backlog=3)
        self.is_running = True
        print("[WEB] Server started on :%d" % self.port)

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
        gc.collect()

    async def handle_client(self, reader, writer):
        try:
            line = await asyncio.wait_for(reader.readline(), 2)
            if not line:
                return
            req = line.decode().strip().split()
            if len(req) < 2:
                return
            method, url = req[0], req[1]

            headers = {}
            while True:
                header = await asyncio.wait_for(reader.readline(), 1)
                if header == b"\r\n" or not header:
                    break
                try:
                    key, value = header.decode().split(":", 1)
                    headers[key.lower()] = value.strip()
                except Exception:
                    pass

            if url.startswith("/api/"):
                await self.api.handle(method, url, reader, writer, headers)
            else:
                await self._serve_static(url, writer)
        except Exception as exc:
            print("[WEB]", exc)
        finally:
            try:
                await writer.aclose()
            except Exception:
                pass
            gc.collect()

    async def _serve_static(self, url, writer):
        url = url.split("?", 1)[0]
        if url == "/":
            url = "/index.html"
        if ".." in url:
            writer.write("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return

        path = "/static" + url
        try:
            size = uos.stat(path)[6]
            ext = url.rsplit(".", 1)[-1]
            mime = {
                "html": "text/html",
                "css": "text/css",
                "js": "application/javascript",
                "json": "application/json",
            }.get(ext, "text/plain")
            writer.write(
                "HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
                % (mime, size)
            )
            await writer.drain()
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(256)
                    if not chunk:
                        break
                    writer.write(chunk)
                    await writer.drain()
                    await asyncio.sleep_ms(1)
        except Exception:
            writer.write("HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")
            await writer.drain()
