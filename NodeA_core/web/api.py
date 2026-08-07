# web/api.py — FS / battery / wifi / power / mem
#
# === RAM RULES (обязательно при любых правках) ===
# 1. Файлы: только readinto(static buf) / chunked write, не f.read() целиком.
# 2. list dir: лимит записей; не строить гигантские list/dict.
# 3. JSON-ответы держать маленькими; не логировать body в RAM.
# 4. Upload: жёсткий _MAX_UPLOAD; писать на flash кусками.
# 5. Новые endpoint: без накопления глобальных ring/list; при нужде — mempool slab.
#

import asyncio
import uos
import ujson as json

_DIR_FLAG = 0x4000
_CHUNK = 512
_WIFI_SETTINGS = "wifi_settings.json"
_FILE_BUF = bytearray(_CHUNK)
_MAX_UPLOAD = 24 * 1024  # hard cap — protects heap on large editor saves


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
                data = await self._read_json_body(reader, headers)
                await self._power_command(data, writer)
            elif endpoint == "mem":
                await self._mem_status(writer)
            elif endpoint == "radio/status":
                await self._radio_status(writer)
            elif endpoint == "radio/cmd" and method == "POST":
                data = await self._read_json_body(reader, headers)
                await self._radio_command(data, writer)
            else:
                await self._send_json(writer, {"error": "not_found"}, status="404 Not Found")
        except Exception as exc:
            await self._send_json(writer, {"error": str(exc)}, status="500 Internal Server Error")

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
        # Cap listing — large dirs blow JSON + heap
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

    async def _read_json_body(self, reader, headers):
        length = int(headers.get("content-length", "0"))
        if length <= 0:
            return {}
        # Hard cap — prevents browser/tool from OOM'ing the device
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
        """powered / online / rf_on — как M_S_v1.0 (без опроса GPIO)."""
        if self.core is None:
            await self._send_json(writer, {"ok": False})
            return
        from hardware.radio_hub import get_radio_hub
        hub = get_radio_hub(self.core)
        if hub.link:
            try:
                hub.link.poll()
            except Exception:
                pass
        snap = hub.snapshot()
        p = snap["power"]
        p["ok"] = True
        p["online"] = snap["link"]["online"]
        await self._send_json(writer, p)

    async def _power_command(self, data, writer):
        """power_on / power_off / rf_on / rf_off / emergency."""
        if self.core is None:
            raise ValueError("no_core")
        from hardware.radio_hub import get_radio_hub
        from modules.power.manager import get_power_manager
        from protocol import MOD_SUBGHZ

        hub = get_radio_hub(self.core)
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
        snap = hub.snapshot()
        p = snap["power"]
        p["ok"] = True
        p["online"] = snap["link"]["online"]
        await self._send_json(writer, p)



    async def _radio_status(self, writer):
        """Always 200 + JSON. Never throw — web must not show permanent err."""
        if self.core is None:
            await self._send_json(writer, {"ok": False, "error": "no_core"})
            return
        try:
            from hardware.radio_hub import get_radio_hub
            hub = get_radio_hub(self.core)
            if hub.link:
                try:
                    hub.link.poll()
                except Exception:
                    pass
            await self._send_json(writer, hub.snapshot())
        except Exception as e:
            await self._send_json(writer, {
                "ok": False,
                "error": str(e),
                "link": {"online": False, "pending": False},
                "power": {"powered": False, "rf_on": False, "q2": False, "q3": False,
                          "node_b_online": False, "last_error": str(e)},
                "module": {"name": "none", "active": False, "state": "idle", "letter": "."},
            })

    async def _radio_command(self, data, writer):
        from hardware.radio_hub import get_radio_hub
        if self.core is None:
            raise ValueError("no_core")
        hub = get_radio_hub(self.core)
        try:
            await hub.command(data)
        except Exception as e:
            await self._send_json(writer, {"ok": False, "error": str(e)})
            return
        if hub.link:
            try:
                hub.link.poll()
            except Exception:
                pass
        await self._send_json(writer, hub.snapshot())

    async def _send_json(self, writer, data, status="200 OK"):
        body = json.dumps(data)
        if isinstance(body, str):
            body = body.encode()
        writer.write(
            b"HTTP/1.1 " + status.encode() + b"\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
        )
        await writer.drain()
