# lib/mempool.py
#
# === RAM RULES (обязательно при любых правках) ===
# 1. Любой буфер >128 B — только register()/acquire(), не «bytearray(n)» в модуле.
# 2. sticky=True пока подсистема жива; drop=True при stop (wifi_off / rf_off / profile switch).
# 3. RF: ensure_rf_workspace — exclusive (один rf_*).
# 4. Не пытаться «сдвигать» кучу — MicroPython так не умеет.
# 5. guard_or_gc() перед тяжёлой работой; critical → отказ, не OOM.
#

# lib/mempool.py — named fixed slabs + soft RAM guard (ESP32-C3 MicroPython)
#
# Truth about MicroPython heap:
#   - Cannot physically compact / move objects.
#   - gc.mem_free() = sum of free fragments, NOT largest contiguous block.
#   - Anti-fragmentation that works:
#       1) allocate large buffers ONCE as named slabs
#       2) never resize them; exclusive RF profile (one workspace)
#       3) single-flight HTTP (one request body at a time)
#       4) gc.collect() after each request + early gc.threshold
#       5) refuse work when free_b < CRITICAL (avoid hard crash/reset)
#
# Profiles:
#   web_tx / web_hdr / web_json — sticky while HTTP server runs
#   link_rx / link_tx           — sticky while link client runs
#   rf_*                        — exclusive, drop on switch

import gc

_POOL = {}

# Soft limits (bytes). Tuned for ESP32-C3 ~160–200KB heap after firmware.
CRITICAL_FREE = 24 * 1024   # refuse new heavy work
WARN_FREE = 40 * 1024       # force extra GC
SAFE_FREE = 64 * 1024


def register(name, size, sticky=True):
    name = str(name)
    size = int(size)
    if size < 16:
        size = 16
    entry = _POOL.get(name)
    if entry is not None and entry.get("buf") is not None and entry["size"] >= size:
        return entry["buf"]
    gc.collect()
    buf = bytearray(size)
    _POOL[name] = {"buf": buf, "size": size, "owner": None, "sticky": bool(sticky)}
    return buf


def acquire(name, owner="?"):
    entry = _POOL.get(name)
    if entry is None or entry.get("buf") is None:
        return None
    if entry["owner"] is not None and entry["owner"] != owner:
        return None
    entry["owner"] = owner
    return entry["buf"]


def release(name, drop=False):
    entry = _POOL.get(name)
    if entry is None:
        return
    entry["owner"] = None
    if drop or not entry.get("sticky", True):
        entry["buf"] = None
        entry["size"] = 0
        gc.collect()
        gc.collect()


def resize_profile(name, new_size, sticky=True):
    release(name, drop=True)
    return register(name, new_size, sticky=sticky)


def switch_rf(from_name, to_name, to_size):
    if from_name and from_name in _POOL:
        release(from_name, drop=True)
    return register(to_name, to_size, sticky=False)


def free_b():
    try:
        return gc.mem_free()
    except Exception:
        return 0


def pressure():
    """'ok' | 'warn' | 'critical' based on total free (not contiguous)."""
    f = free_b()
    if f < CRITICAL_FREE:
        return "critical"
    if f < WARN_FREE:
        return "warn"
    return "ok"


def guard_or_gc():
    """Call before heavy alloc. Returns False if still critical after GC."""
    p = pressure()
    if p == "ok":
        return True
    gc.collect()
    gc.collect()
    return pressure() != "critical"


def snapshot():
    f = free_b()
    items = []
    reserved = 0
    for name, e in _POOL.items():
        sz = e.get("size") or 0
        alive = e.get("buf") is not None
        if alive:
            reserved += sz
        items.append({
            "name": name,
            "size": sz,
            "busy": e.get("owner") is not None,
            "owner": e.get("owner") or "",
            "alive": alive,
            "sticky": bool(e.get("sticky")),
        })
    return {
        "free_b": f,
        "reserved_b": reserved,
        "pressure": pressure(),
        "critical_b": CRITICAL_FREE,
        "slabs": items,
    }


def ensure_web_slabs():
    register("web_tx", 512, sticky=True)
    register("web_hdr", 192, sticky=True)
    register("web_json", 768, sticky=True)


def ensure_link_slabs():
    register("link_rx", 520, sticky=True)
    register("link_tx", 520, sticky=True)


def ensure_rf_workspace(kind):
    for n in list(_POOL.keys()):
        if n.startswith("rf_"):
            release(n, drop=True)
    if kind == "cc1101":
        return register("rf_cc1101", 768, sticky=False)
    if kind == "nrf":
        return register("rf_nrf", 512, sticky=False)
    return None
