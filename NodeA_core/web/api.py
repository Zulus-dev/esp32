# web/api.py — FS / battery / wifi / power / mem / radio(binary)
#
# RAM RULES:
# 1. Files: readinto(static buf) only.
# 2. Radio status/cmd/log = application/octet-stream (binary). Phone decodes.
# 3. JSON only for: wifi_settings, FS list/ok, battery, mem (rare).
# 4. No dict snapshot on radio hot path.
#

import asyncio
import uos
import ujson as json

_DIR_FLAG = 0x4000
_CHUNK = 512
_WIFI_SETTINGS = "wifi_settings.json"
_FILE_BUF = bytearray(_CHUNK)
_MAX_UPLOAD = 24 * 1024


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
                await self._send_json(writer, self._list_dir(params.get("path", "/")))
            elif endpoint == "read":
                await self._read_file(params.get("path"), writer)
            elif endpoint in ("upload", "save") and method == "POST":
                await self._write_file(params.get("path"), reader, writer, headers)
            elif endpoint == "mkdir":
                self._mkdir(params.get("path"))
                await self._send_json(writer, {"ok": True})
            elif endpoint == "touch":
                self._touch(params.get("path"))
                await self._send_json(writer, {"ok": True})
            elif endpoint == "delete":
                self._delete(params.get("path"))
                await self._send_json(writer, {"ok": True})
            elif endpoint == "move":
                self._move(params.get("src"), params.get("dst"))
                await self._send_json(writer, {"ok": True})
            elif endpoint == "ping":
                await self._send_json(writer, {"ok": True})
            elif endpoint == "battery/status":
                await self._battery_status(writer)
            elif endpoint == "wifi_settings":
                if method == "GET":
                    await self._send_json(writer, self._load_wifi_settings())
                elif method == "POST":
                    data = await self._read_json_body(reader, headers)
                    await self._save_wifi_settings(data, writer)
                else:
                    await self._send_json(writer, {"error": "method_not_allowed"}, status="405 Method Not Allowed")
            elif endpoint == "set_wifi" and method == "POST":
                data = await self._read_json_body(reader, headers)
                await self._save_wifi_settings(data, writer)
            elif endpoint == "power/status":
                await self._power_status(writer)
            elif endpoint == "power/cmd" and method == "POST":
                data = await self._read_body_auto(reader, headers)
                await self._power_command(data, writer)
            elif endpoint == "mem":
                await self._mem_status(writer)
            elif endpoint == "radio/status":
                await self._radio_status(writer)
            elif endpoint == "radio/cmd" and method == "POST":
                data = await self._read_body_auto(reader, headers)
                await self._radio_command(data, writer)
            elif endpoint == "radio/log" and method == "POST":
                data = await self._read_body_auto(reader, headers)
                await self._radio_log(data, writer)
            else:
                await self._send_json(writer, {"error": "not_found"}, status="404 Not Found")
        except Exception as exc:
            try:
                await self._send_json(writer, {"error": str(exc)}, status="500 Internal Server Error")
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

    def _parent(self, path):
        if path == "/":
            return "/"
        parent = path.rstrip("/").rsplit("/", 1)[0]
        return parent if parent else "/"

    def _is_dir_stat(self, stat):
        return (stat[0] & _DIR_FLAG) != 0

    def _list_dir(self, path):
        path = self._safe_path(path)
        items = []
        if path != "/":
            items.append({"name": "..", "path": self._parent(path), "type": "back", "dir": True, "size": 0})
        names = uos.listdir(path)
        try:
            names.sort()
        except Exception:
            pass
        nmax = 48
        for name in names[:nmax]:
            full = self._join(path, name)
            try:
                stat = uos.stat(full)
                is_dir = self._is_dir_stat(stat)
                size = 0 if is_dir else stat[6]
            except Exception:
                is_dir = False
                size = 0
            items.append({"name": name, "path": full, "type": "dir" if is_dir else "file", "dir": is_dir, "size": size})
        out = {"path": path, "items": items}
        if len(names) > nmax:
            out["truncated"] = True
            out["total"] = len(names)
        return out

    async def _read_file(self, path, writer):
        path = self._safe_path(path)
        try:
            size = uos.stat(path)[6]
        except Exception:
            size = 0
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: "
            + str(size).encode()
            + b"\r\nConnection: close\r\n\r\n"
        )
        await writer.drain()
        buf = _FILE_BUF
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
        await self._send_json(writer, {"ok": True, "path": path})

    def _mkdir(self, path):
        uos.mkdir(self._safe_path(path))

    def _touch(self, path):
        with open(self._safe_path(path), "ab"):
            pass

    def _move(self, src, dst):
        uos.rename(self._safe_path(src), self._safe_path(dst))

    def _delete(self, path):
        path = self._safe_path(path)
        if path == "/":
            raise ValueError("refuse_root_delete")
        stat = uos.stat(path)
        if self._is_dir_stat(stat):
            uos.rmdir(path)
        else:
            uos.remove(path)

    async def _read_body_auto(self, reader, headers):
        length = int(headers.get("content-length", "0"))
        if length <= 0:
            return b""
        if length > 4096:
            raise ValueError("body_too_large")
        raw = await reader.read(length)
        if not raw:
            return b""
        if raw[:1] in (b"{", b"["):
            try:
                return json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            except Exception:
                return raw
        return raw

    async def _read_json_body(self, reader, headers):
        length = int(headers.get("content-length", "0"))
        if length <= 0:
            return {}
        if length > 4096:
            raise ValueError("body_too_large")
        raw = await reader.read(length)
        if not raw:
            return {}
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)

    def _load_wifi_settings(self):
        try:
            with open(_WIFI_SETTINGS, "r") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        return {
            "essid": cfg.get("essid", "ColibryOS"),
            "password": cfg.get("password", ""),
            "hidden": bool(cfg.get("hidden", False)),
        }

    async def _save_wifi_settings(self, data, writer):
        essid = str(data.get("essid", data.get("ssid", "ColibryOS"))).strip() or "ColibryOS"
        password = str(data.get("password", ""))
        hidden = bool(data.get("hidden", False))
        if len(essid) > 32:
            raise ValueError("ssid_too_long")
        if password and len(password) < 8:
            raise ValueError("password_min_8")
        if len(password) > 63:
            raise ValueError("password_too_long")
        cfg = {"essid": essid, "password": password, "hidden": hidden}
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
        battery = self.core.services.get("battery") if self.core else None
        if battery is None:
            await self._send_json(writer, {"ok": False, "error": "battery_monitor_missing"})
        else:
            await self._send_json(writer, battery.read())

    async def _mem_status(self, writer):
        try:
            from lib import mempool
            await self._send_json(writer, mempool.snapshot())
        except Exception as e:
            await self._send_json(writer, {"error": str(e)})

    async def _power_status(self, writer):
        if self.core is None:
            await self._send_bin(writer, bytes((0, 0, 0, 0)))
            return
        from hardware.radio_hub import get_radio_hub
        hub = get_radio_hub(self.core)
        if hub.link:
            try:
                hub.link.poll()
            except Exception:
                pass
        # binary: powered, rf_on, online, err
        pwr = hub._pwr()
        online = 1 if (hub.link and hub.link.online()) else 0
        await self._send_bin(writer, bytes((
            1 if pwr.powered else 0,
            1 if pwr.rf_powered else 0,
            online,
            hub.err & 0xFF,
        )))

    async def _power_command(self, data, writer):
        if self.core is None:
            raise ValueError("no_core")
        from hardware.radio_hub import get_radio_hub
        from modules.power.manager import get_power_manager
        from protocol import MOD_SUBGHZ

        hub = get_radio_hub(self.core)
        # binary: 1 byte action 1=b_on 2=b_off 3=rf_on 4=rf_off 5=emergency
        # or dict legacy
        action = ""
        if isinstance(data, (bytes, bytearray, memoryview)) and len(data) > 0:
            code = data[0]
            action = {1: "power_on", 2: "power_off", 3: "rf_on", 4: "rf_off", 5: "emergency"}.get(code, "")
        elif isinstance(data, dict):
            action = data.get("action", "")
        if action in ("node_b_on", "power_on"):
            await hub.node_b_on()
        elif action in ("node_b_off", "power_off"):
            await hub.node_b_off()
        elif action == "rf_on":
            await hub.module_on(MOD_SUBGHZ)
        elif action == "rf_off":
            await hub.module_off(rail_off=True)
        elif action == "emergency":
            try:
                hub.close()
            except Exception:
                pass
            await get_power_manager(self.core).emergency_cut()
        else:
            raise ValueError("unknown_power_action")
        if hub.link:
            try:
                hub.link.poll()
            except Exception:
                pass
        pwr = hub._pwr()
        online = 1 if (hub.link and hub.link.online()) else 0
        await self._send_bin(writer, bytes((
            1 if pwr.powered else 0,
            1 if pwr.rf_powered else 0,
            online,
            hub.err & 0xFF,
        )))

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
            mv = hub.pack_status(include_events=True, max_events=8)
            await self._send_bin(writer, mv)
        except Exception:
            await self._send_bin(writer, b"\x00\x00")

    async def _radio_command(self, data, writer):
        from hardware.radio_hub import get_radio_hub
        from protocol import MOD_SUBGHZ, WA_LOG_SAVE, WA_LOG_CLEAR
        if self.core is None:
            await self._send_bin(writer, b"\x00\x00")
            return
        hub = get_radio_hub(self.core)
        try:
            if isinstance(data, (bytes, bytearray, memoryview)):
                buf = data
                action = buf[0] if len(buf) > 0 else 0
                mod_id = buf[1] if len(buf) > 1 else MOD_SUBGHZ
                fk = 433920
                if len(buf) >= 6:
                    fk = (buf[2] << 24) | (buf[3] << 16) | (buf[4] << 8) | buf[5]
                preset = buf[6] if len(buf) > 6 else 0
                thresh = buf[7] if len(buf) > 7 else 72
                raw = None
                if len(buf) > 8:
                    raw = memoryview(buf)[8:]
                if action == WA_LOG_CLEAR:
                    hub.clear_log()
                elif action == WA_LOG_SAVE:
                    hub.save_session(False)
                else:
                    await hub.command_bin(action, mod_id, fk, preset, thresh, raw)
            elif isinstance(data, dict):
                act = data.get("action", "")
                if act in ("clear", "log_clear"):
                    hub.clear_log()
                elif act in ("save", "log_save"):
                    hub.save_session(bool(data.get("clear", False)))
                else:
                    await hub.command(data)
        except Exception:
            pass
        if hub.link:
            try:
                hub.link.poll()
            except Exception:
                pass
        mv = hub.pack_status(include_events=True, max_events=8)
        await self._send_bin(writer, mv)

    async def _radio_log(self, data, writer):
        from hardware.radio_hub import get_radio_hub
        if self.core is None:
            await self._send_bin(writer, b"\x00")
            return
        hub = get_radio_hub(self.core)
        clear = False
        if isinstance(data, (bytes, bytearray, memoryview)):
            clear = len(data) > 0 and data[0] == 1
        elif isinstance(data, dict):
            clear = data.get("action") == "clear"
        if clear:
            hub.clear_log()
            await self._send_bin(writer, b"\x01")
            return
        ok, path, n = hub.save_session(False)
        if ok:
            pb = path.encode() if isinstance(path, str) else b""
            out = bytearray(1 + len(pb))
            out[0] = 1
            out[1:] = pb
            await self._send_bin(writer, out)
        else:
            await self._send_bin(writer, b"\x00")

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
        body = json.dumps(data)
        if isinstance(body, str):
            body = body.encode()
        writer.write(
            b"HTTP/1.1 " + status.encode() + b"\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
        )
        await writer.drain()
