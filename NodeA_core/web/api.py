# web/api.py — FS / battery / wifi_settings / radio (binary HTTP)
#
# Protocol rules:
# 1. Radio / FS / battery / errors = application/octet-stream.
# 2. JSON only for wifi_settings.
# 3. Buffers: mempool web_list / web_file (sticky while server runs). No module-level bytearray.
# 4. Endpoints used by static UI only. Power via radio/cmd (WA_NODE_B_* / WA_MOD_*).
#

import asyncio
import uos
import ujson as json

from protocol_web import (
    FS_LIST_MAGIC, FS_TYPE_FILE, FS_TYPE_DIR, FS_TYPE_BACK,
    BIN_OK, BIN_ERR,
    WA_LOG_CLEAR, WA_LOG_SAVE,
)

_DIR_FLAG = 0x4000
_CHUNK = 1024
_WIFI_SETTINGS = "wifi_settings.json"
_MAX_UPLOAD = 24 * 1024


def _web_list_buf():
    from lib import mempool
    buf = mempool.acquire("web_list", owner="web")
    if buf is None:
        buf = mempool.register("web_list", 1024, sticky=True)
        mempool.acquire("web_list", owner="web")
    return buf


def _web_file_buf():
    from lib import mempool
    buf = mempool.acquire("web_file", owner="web")
    if buf is None:
        buf = mempool.register("web_file", 1024, sticky=True)
        mempool.acquire("web_file", owner="web")
    return buf


class FileAPI:
    def __init__(self, core=None):
        self.core = core

    def set_core(self, core):
        self.core = core

    async def handle(self, method, url, reader, writer, headers=None):
        headers = headers or {}
        params = self._query_params(url)
        endpoint = url.split("?", 1)[0].replace("/api/", "", 1)
        try:
            if endpoint == "list":
                await self._send_bin(writer, self._list_dir_bin(params.get("path", "/")))
            elif endpoint == "read":
                await self._read_file(params.get("path"), writer)
            elif endpoint in ("upload", "save") and method == "POST":
                await self._write_file(params.get("path"), reader, writer, headers)
            elif endpoint == "mkdir":
                self._mkdir(params.get("path"))
                await self._send_bin(writer, bytes((BIN_OK,)))
            elif endpoint == "touch":
                self._touch(params.get("path"))
                await self._send_bin(writer, bytes((BIN_OK,)))
            elif endpoint == "delete":
                self._delete(params.get("path"))
                await self._send_bin(writer, bytes((BIN_OK,)))
            elif endpoint == "move":
                self._move(params.get("src"), params.get("dst"))
                await self._send_bin(writer, bytes((BIN_OK,)))
            elif endpoint == "battery/status":
                await self._battery_status(writer)
            elif endpoint == "wifi_settings":
                if method == "GET":
                    await self._send_json(writer, self._load_wifi_settings())
                elif method == "POST":
                    data = await self._read_json_body(reader, headers)
                    await self._save_wifi_settings(data, writer)
                else:
                    await self._send_bin(writer, bytes((BIN_ERR, 0x05)), status="405 Method Not Allowed")
            elif endpoint == "radio/status":
                await self._radio_status(writer)
            elif endpoint == "radio/capture":
                await self._radio_capture(writer)
            elif endpoint == "radio/cmd" and method == "POST":
                data = await self._read_body_bin(reader, headers)
                await self._radio_command(data, writer)
            else:
                await self._send_bin(writer, bytes((BIN_ERR, 0x04)), status="404 Not Found")
        except Exception:
            try:
                await self._send_bin(writer, bytes((BIN_ERR, 0x01)), status="500 Internal Server Error")
            except Exception:
                pass

    def _query_params(self, url):
        params = {}
        if "?" not in url:
            return params
        for pair in url.split("?", 1)[1].split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params[self._url_decode(k)] = self._url_decode(v)
        return params

    def _url_decode(self, value):
        value = value.replace("+", " ")
        out = []
        i = 0
        while i < len(value):
            if value[i] == "%" and i + 2 < len(value):
                try:
                    out.append(chr(int(value[i + 1:i + 3], 16)))
                    i += 3
                    continue
                except Exception:
                    pass
            out.append(value[i])
            i += 1
        return "".join(out)

    def _safe_path(self, path):
        if not path:
            raise ValueError("path_required")
        path = path.replace("//", "/")
        if ".." in path:
            raise ValueError("invalid_path")
        if not path.startswith("/"):
            path = "/" + path
        return path

    def _join(self, base, name):
        return (base.rstrip("/") + "/" + name) if base != "/" else "/" + name

    def _is_dir_stat(self, stat):
        return (stat[0] & _DIR_FLAG) != 0

    def _list_dir_bin(self, path):
        """Binary FS list: magic_u16 | path_len | path | count | entries."""
        path = self._safe_path(path)
        buf = _web_list_buf()
        pos = 0
        buf[0] = (FS_LIST_MAGIC >> 8) & 0xFF
        buf[1] = FS_LIST_MAGIC & 0xFF
        pb = path.encode()
        pl = len(pb)
        if pl > 64:
            pl = 64
        buf[2] = pl
        pos = 3
        buf[pos:pos + pl] = pb[:pl]
        pos += pl
        count_pos = pos
        pos += 1
        count = 0
        nmax = 40

        def _add(typ, size, name):
            nonlocal pos, count
            nb = name.encode() if isinstance(name, str) else name
            nl = len(nb)
            if nl > 48:
                nl = 48
            if pos + 1 + 4 + 1 + nl > len(buf):
                return False
            buf[pos] = typ
            pos += 1
            buf[pos] = (size >> 24) & 0xFF
            buf[pos + 1] = (size >> 16) & 0xFF
            buf[pos + 2] = (size >> 8) & 0xFF
            buf[pos + 3] = size & 0xFF
            pos += 4
            buf[pos] = nl
            pos += 1
            buf[pos:pos + nl] = nb[:nl]
            pos += nl
            count += 1
            return True

        if path != "/":
            _add(FS_TYPE_BACK, 0, b"..")
        try:
            names = uos.listdir(path)
        except Exception:
            names = []
        try:
            names.sort()
        except Exception:
            pass
        for name in names:
            if count >= nmax:
                break
            full = self._join(path, name)
            try:
                stat = uos.stat(full)
                is_dir = self._is_dir_stat(stat)
                size = 0 if is_dir else int(stat[6])
            except Exception:
                is_dir = False
                size = 0
            if not _add(FS_TYPE_DIR if is_dir else FS_TYPE_FILE, size & 0xFFFFFFFF, name):
                break
        buf[count_pos] = count & 0xFF
        return memoryview(buf)[:pos]

    async def _read_file(self, path, writer):
        path = self._safe_path(path)
        try:
            size = uos.stat(path)[6]
        except Exception:
            size = 0
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Length: "
            + str(size).encode()
            + b"\r\nConnection: close\r\n\r\n"
        )
        await writer.drain()
        buf = _web_file_buf()
        with open(path, "rb") as f:
            while True:
                n = f.readinto(buf)
                if not n:
                    break
                writer.write(memoryview(buf)[:n])
                await writer.drain()
                await asyncio.sleep_ms(0)

    async def _write_file(self, path, reader, writer, headers):
        path = self._safe_path(path)
        length = int(headers.get("content-length", "0"))
        if length < 0 or length > _MAX_UPLOAD:
            raise ValueError("body_too_large")
        tmp = path + ".tmp"
        remaining = length
        with open(tmp, "wb") as f:
            while remaining > 0:
                chunk = await reader.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        if remaining != 0:
            try:
                uos.remove(tmp)
            except Exception:
                pass
            raise ValueError("short_body")
        try:
            uos.remove(path)
        except Exception:
            pass
        uos.rename(tmp, path)
        await self._send_bin(writer, bytes((BIN_OK,)))

    def _mkdir(self, path):
        uos.mkdir(self._safe_path(path))

    def _touch(self, path):
        with open(self._safe_path(path), "ab"):
            pass

    def _move(self, src, dst):
        uos.rename(self._safe_path(src), self._safe_path(dst))

    def _delete(self, path):
        path = self._safe_path(path)
        try:
            st = uos.stat(path)
            if self._is_dir_stat(st):
                uos.rmdir(path)
            else:
                uos.remove(path)
        except Exception:
            uos.remove(path)

    async def _read_body_bin(self, reader, headers):
        length = int(headers.get("content-length", "0"))
        if length <= 0:
            return b""
        if length > _MAX_UPLOAD:
            raise ValueError("body_too_large")
        data = await reader.read(length)
        if len(data) < length:
            raise ValueError("short_body")
        return data

    async def _read_json_body(self, reader, headers):
        """Only for wifi_settings."""
        length = int(headers.get("content-length", "0"))
        if length <= 0 or length > 2048:
            raise ValueError("bad_json_body")
        raw = await reader.read(length)
        return json.loads(raw)

    def _load_wifi_settings(self):
        try:
            with open(_WIFI_SETTINGS, "r") as f:
                cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else {}
        except Exception:
            return {"essid": "", "password": "", "hidden": False}

    async def _save_wifi_settings(self, data, writer):
        if not isinstance(data, dict):
            raise ValueError("bad_settings")
        cfg = {
            "essid": str(data.get("essid", ""))[:32],
            "password": str(data.get("password", ""))[:63],
            "hidden": bool(data.get("hidden", False)),
        }
        tmp = _WIFI_SETTINGS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f)
        try:
            uos.remove(_WIFI_SETTINGS)
        except Exception:
            pass
        uos.rename(tmp, _WIFI_SETTINGS)
        await self._send_json(writer, {"ok": True, "settings": cfg, "restart_ap": True})

    async def _battery_status(self, writer):
        # 10 B: ok, percent, status_code, trend_i8, mv_u16, min_u16, max_u16
        out = bytearray(10)
        battery = self.core.services.get("battery") if self.core else None
        if battery is None:
            await self._send_bin(writer, out)
            return
        try:
            battery.read()
        except Exception:
            pass
        out[0] = 1 if battery.ok else 0
        out[1] = int(battery.percent) & 0xFF
        st = battery.status or "UNKNOWN"
        sc = 0
        if st == "CRITICAL":
            sc = 1
        elif st == "LOW":
            sc = 2
        elif st == "OK":
            sc = 3
        elif st == "FULL":
            sc = 4
        out[2] = sc
        tr = battery.trend or "?"
        ti = 0
        if tr == "UP" or tr == "+":
            ti = 1
        elif tr == "DOWN" or tr == "-":
            ti = 255
        out[3] = ti
        mv = int(battery.voltage_mv) & 0xFFFF
        out[4] = (mv >> 8) & 0xFF
        out[5] = mv & 0xFF
        mn = int(battery.min_mv) & 0xFFFF
        out[6] = (mn >> 8) & 0xFF
        out[7] = mn & 0xFF
        mx = int(battery.max_mv) & 0xFFFF
        out[8] = (mx >> 8) & 0xFF
        out[9] = mx & 0xFF
        await self._send_bin(writer, out)

    async def _radio_status(self, writer):
        if self.core is None:
            await self._send_bin(writer, b"\x00\x00")
            return
        try:
            from hardware.radio_hub import get_radio_hub
            hub = get_radio_hub(self.core)
            if hub.link:
                try:
                    hub.link.poll()
                except Exception:
                    pass
            mv = hub.pack_status(include_events=False, max_events=0)
            await self._send_bin(writer, mv)
        except Exception:
            await self._send_bin(writer, b"\x00\x00")

    async def _radio_capture(self, writer):
        """
        Full RAW only on NodeB. NodeA = transport:
          pipe_begin → OP_DUMP → STREAM chunks → HTTP body → pipe_end.
        No 8KB slab on A. Wire: freq_u32be + rssi_i8 + nbits_u16be + len_u16be + data[len]
        """
        if self.core is None:
            await self._send_bin(writer, b"")
            return
        try:
            from hardware.radio_hub import get_radio_hub
            from protocol import OP_DUMP, MOD_SUBGHZ
            import time

            hub = get_radio_hub(self.core)
            link = hub.link
            if link is None or not getattr(link, "capture_ready", False):
                await self._send_bin(writer, b"")
                return

            link.pipe_begin()
            try:
                cmd = bytearray(1)
                cmd[0] = OP_DUMP
                link.send_cmd(hub.active_mod or MOD_SUBGHZ, memoryview(cmd)[:1])
            except Exception:
                link.pipe_end()
                await self._send_bin(writer, b"")
                return

            end_ms = time.ticks_ms() + 2500
            while not link._pipe_meta and time.ticks_diff(end_ms, time.ticks_ms()) > 0:
                try:
                    link.poll()
                except Exception:
                    pass
                await asyncio.sleep_ms(3)

            if not link._pipe_meta or link._pipe_total <= 0:
                link.pipe_end()
                await self._send_bin(writer, b"")
                return

            freq = link._pipe_freq
            rssi = link._pipe_rssi
            nbits = link._pipe_bits
            n = link._pipe_total
            if nbits > 65535:
                nbits = 65535
            hdr = bytearray(9)
            hdr[0] = (freq >> 24) & 255
            hdr[1] = (freq >> 16) & 255
            hdr[2] = (freq >> 8) & 255
            hdr[3] = freq & 255
            hdr[4] = rssi & 255
            hdr[5] = (nbits >> 8) & 255
            hdr[6] = nbits & 255
            hdr[7] = (n >> 8) & 255
            hdr[8] = n & 255
            total = 9 + n
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n"
                b"Content-Length: " + str(total).encode()
                + b"\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n"
            )
            writer.write(hdr)
            await writer.drain()

            got = 0
            end_ms = time.ticks_ms() + 6000
            while got < n and time.ticks_diff(end_ms, time.ticks_ms()) > 0:
                try:
                    link.poll()
                except Exception:
                    pass
                while True:
                    mv = link.pipe_pop()
                    if mv is None:
                        break
                    if len(mv) <= 1:
                        continue
                    data = mv[1:]
                    take = len(data)
                    room = n - got
                    if take > room:
                        take = room
                    if take > 0:
                        writer.write(data[:take])
                        await writer.drain()
                        got += take
                if link._pipe_end and link._pipe_count == 0:
                    break
                await asyncio.sleep_ms(1)

            overflow = bool(getattr(link, "_pipe_overflow", False))
            link.pipe_end()
            if got == n and not overflow:
                link.capture_ready = False
        except Exception:
            try:
                if self.core:
                    from hardware.radio_hub import get_radio_hub
                    link = get_radio_hub(self.core).link
                    if link:
                        link.pipe_end()
            except Exception:
                pass
            try:
                await self._send_bin(writer, b"")
            except Exception:
                pass

    async def _radio_command(self, data, writer):
        from hardware.radio_hub import get_radio_hub
        from protocol import MOD_SUBGHZ
        if self.core is None:
            await self._send_bin(writer, b"\x00\x00")
            return
        hub = get_radio_hub(self.core)
        try:
            if not isinstance(data, (bytes, bytearray, memoryview)) or len(data) < 1:
                raise ValueError("bad_radio_cmd")
            buf = data
            action = buf[0]
            mod_id = buf[1] if len(buf) > 1 else MOD_SUBGHZ
            fk = 433920
            if len(buf) >= 6:
                fk = (buf[2] << 24) | (buf[3] << 16) | (buf[4] << 8) | buf[5]
            preset = buf[6] if len(buf) > 6 else 0
            thresh = buf[7] if len(buf) > 7 else 72
            raw = memoryview(buf)[8:] if len(buf) > 8 else None
            if action == WA_LOG_CLEAR:
                hub.clear_log()
            elif action != WA_LOG_SAVE:
                await hub.command_bin(action, mod_id, fk, preset, thresh, raw)
        except Exception:
            pass
        if hub.link:
            try:
                hub.link.poll()
            except Exception:
                pass
        mv = hub.pack_status(include_events=False, max_events=0)
        await self._send_bin(writer, mv)

    async def _send_bin(self, writer, data, status="200 OK"):
        if data is None:
            data = b""
        n = len(data)
        writer.write(
            b"HTTP/1.1 " + status.encode()
            + b"\r\nContent-Type: application/octet-stream\r\nContent-Length: "
            + str(n).encode()
            + b"\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n"
        )
        if n:
            writer.write(data)
        await writer.drain()

    async def _send_json(self, writer, data, status="200 OK"):
        """Only for wifi_settings."""
        body = json.dumps(data)
        if isinstance(body, str):
            body = body.encode()
        writer.write(
            b"HTTP/1.1 " + status.encode() + b"\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
        )
        await writer.drain()
