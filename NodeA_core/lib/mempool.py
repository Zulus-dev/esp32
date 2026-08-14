# lib/mempool.py — named fixed slabs + soft RAM guard (ESP32-C3 MicroPython)
#
# === RAM RULES ===
# 1. Буфер >128 B — только register()/acquire(), не bytearray(n) в модулях.
# 2. sticky=True пока подсистема жива; drop=True при stop (wifi_off / rf_off).
# 3. RF: ensure_rf_workspace — exclusive (один rf_*).
# 4. MicroPython heap не компактируется; gc.mem_free() = сумма фрагментов.
# 5. guard_or_gc() перед тяжёлой работой; critical → отказ, не OOM.
#
# Profiles:
#   web_tx / web_list / web_file — sticky while HTTP server runs
#   rf_*                          — exclusive, drop on switch
#   Full RAW on NodeB only — NodeA has no relay_cap slab
#

import gc

_POOL = {}

# Soft limits (bytes). Tuned for ESP32-C3 ~160–200KB heap after firmware.
CRITICAL_FREE = 24 * 1024
WARN_FREE = 40 * 1024
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
    """Sticky HTTP slabs while AsyncServer is running."""
    register("web_tx", 1024, sticky=True)
    register("web_list", 1024, sticky=True)
    register("web_file", 1024, sticky=True)


def ensure_link_slabs():
    pass  # link uses fixed arena inside LinkClient


def ensure_rf_workspace(kind):
    for n in list(_POOL.keys()):
        if n.startswith("rf_"):
            release(n, drop=True)
    if kind == "cc1101":
        return register("rf_cc1101", 128, sticky=False)
    if kind == "nrf":
        return register("rf_nrf", 64, sticky=False)
    return None


def ensure_relay_capture(needed=None):
    """Deprecated — full frame is on NodeB; NodeA pipes STREAM chunks only."""
    return None


def drop_relay_capture():
    release("relay_cap", drop=True)
