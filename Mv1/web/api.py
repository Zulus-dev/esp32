# web/api.py - lightweight JSON/File/Radio API; browser owns UI/rendering logic
import gc
import uos
import ujson as json


_DIR_FLAG = 0x4000
_CHUNK = 256
_WIFI_SETTINGS = "wifi_settings.json"
_RATE_LIMIT_MS = 250



class FileAPI:
    def __init__(self, core=None):
        self.core = core
        self._last_radio_cmd_ms = 0

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
            elif endpoint == "wifi_settings":
                if method == "GET":
                    await self._send_json(writer, self._load_wifi_settings())
                elif method == "POST":
                    data = await self._read_json_body(reader, headers)
                    await self._save_wifi_settings(data, writer)
                else:
                    await self._send_json(writer, {"error": "method_not_allowed"}, status="405 Method Not Allowed")
            elif endpoint == "set_wifi" and method == "POST":
                # Backward-compatible endpoint used by the existing settings page.
                data = await self._read_json_body(reader, headers)
                await self._save_wifi_settings(data, writer)
            elif endpoint == "radio/status":
                await self._radio_status(writer)
            elif endpoint == "radio/cmd" and method == "POST":
                data = await self._read_json_body(reader, headers)
                await self._radio_command(data, writer)
            else:
                await self._send_json(writer, {"error": "not_found"}, status="404 Not Found")
        except Exception as exc:
            await self._send_json(writer, {"error": str(exc)}, status="500 Internal Server Error")
        finally:
            gc.collect()

    def _query_params(self, url):
        params = {}
        if "?" not in url:
            return params
        query = url.split("?", 1)[1]
        for pair in query.split("&"):
            if "=" in pair:
                key, value = pair.split("=", 1)
                params[self._url_decode(key)] = self._url_decode(value)
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
        for name in names:
            full = self._join(path, name)
            try:
                stat = uos.stat(full)
                is_dir = self._is_dir_stat(stat)
                size = 0 if is_dir else stat[6]
            except Exception:
                is_dir = False
                size = 0
            items.append({
                "name": name,
                "path": full,
                "type": "dir" if is_dir else "file",
                "dir": is_dir,
                "size": size,
            })
        return {"path": path, "items": items}

    async def _read_file(self, path, writer):
        path = self._safe_path(path)
        writer.write("HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n")
        await writer.drain()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(_CHUNK)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()

    async def _write_file(self, path, reader, writer, headers):
        path = self._safe_path(path)
        length = int(headers.get("content-length", "0"))
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
        path = self._safe_path(path)
        with open(path, "ab"):
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

    def _radio(self):
        if self.core is None:
            return None
        return self.core.services.get("radio")

    async def _radio_status(self, writer):
        radio = self._radio()
        if radio is None:
            await self._send_json(writer, {"ok": True, "powered": False, "online": False, "events": []})
        else:
            await self._send_json(writer, radio.snapshot())

    async def _radio_command(self, data, writer):
        from modules.power.manager import get_power_manager
        pwr = get_power_manager(self.core)
        now = _ticks_ms()
        if now - self._last_radio_cmd_ms < _RATE_LIMIT_MS:
            raise ValueError("rate_limited")
        self._last_radio_cmd_ms = now
        radio = self._radio()
        action = data.get("action", "")
        if action == "power_on":
            radio = await pwr.ensure_b_online()
        elif radio is None:
            raise ValueError("radio_not_started")
        elif action == "power_off":
            await pwr.graceful_shutdown_all()
            radio = self._radio()
        elif action == "stop":
            radio.command(0x08)
        elif action == "subghz_scan":
            radio = await pwr.ensure_rf_on(); radio.command(0x0A, _subghz_payload(data))
        elif action == "subghz_sniff":
            radio = await pwr.ensure_rf_on(); radio.command(0x0B, _subghz_payload(data))
        elif action == "spectrum_sweep":
            radio = await pwr.ensure_rf_on(); radio.command(0x0F, _subghz_payload(data))
        elif action == "set_freq":
            radio = await pwr.ensure_rf_on(); radio.command(0x01, _pack_freq(data.get("freq", 433.92)))
        elif action == "set_modulation":
            radio = await pwr.ensure_rf_on(); radio.command(0x09, _subghz_payload(data))
        elif action == "set_power":
            radio = await pwr.ensure_rf_on(); radio.command(0x0E, _subghz_payload(data))
        elif action == "raw_tx":
            radio = await pwr.ensure_rf_on(); radio.command(0x0D, _subghz_payload(data))
        elif action == "replay":
            radio = await pwr.ensure_rf_on(); radio.command(0x0C, _subghz_payload(data))
        elif action == "nrf_honeypot":
            radio = await pwr.ensure_rf_on(); radio.command(0x06)
        elif action == "ble_sniff":
            radio.command(0x07)
        elif action == "deauth":
            radio.command(0x04, str(data.get("target", "")).encode())
        elif action == "beacon_spam":
            radio.command(0x05, str(data.get("ssid", "Colibry")).encode())
        elif action == "reboot":
            radio.command(0xFE)
        else:
            raise ValueError("unknown_radio_action")
        await self._send_json(writer, radio.snapshot() if radio else {"ok": True})

    async def _send_json(self, writer, data, status="200 OK"):
        body = json.dumps(data)
        writer.write(
            "HTTP/1.1 %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
            % (status, len(body), body)
        )
        await writer.drain()


def _pack_freq(freq):
    khz = int(float(freq) * 1000)
    return bytes(((khz >> 24) & 0xFF, (khz >> 16) & 0xFF, (khz >> 8) & 0xFF, khz & 0xFF))


def _subghz_payload(data):
    keys = ("freq", "mod", "modulation", "baud", "dev", "deviation", "bw", "bandwidth", "sync", "len", "packet_len", "power", "data")
    parts = []
    for key in keys:
        if key in data:
            val = str(data.get(key, ""))[:96]
            parts.append("%s=%s" % (key, val))
    return ";".join(parts).encode()[:240]


def _ticks_ms():
    try:
        import time
        return time.ticks_ms()
    except Exception:
        import time
        return int(time.time() * 1000)
