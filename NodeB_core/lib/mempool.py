# lib/mempool.py — Node B RF slabs + dual capture slots (ping-pong).
#
# Capture contract:
#   CAPTURE_SLOT_SIZE = 4096, CAPTURE_SLOTS = 2 (only on NodeB).
#   Alloc once on RF profile load; drop on RF off. No per-frame bytearray().
#   Fallback 2048 → 1024 per slot if MemoryError.
#
import gc

_POOL = {}

CAPTURE_SLOT_SIZE = 4096
CAPTURE_SLOTS = 2


def register(name, size, sticky=False):
    e = _POOL.get(name)
    if e and e["buf"] is not None and e["size"] >= size:
        return e["buf"]
    gc.collect()
    try:
        b = bytearray(size)
    except MemoryError:
        gc.collect()
        gc.collect()
        return None
    _POOL[name] = {"buf": b, "size": size, "owner": None, "sticky": sticky}
    return b


def acquire(name, owner):
    e = _POOL.get(name)
    if not e or e["buf"] is None:
        return None
    if e["owner"] not in (None, owner):
        return None
    e["owner"] = owner
    return e["buf"]


def release(name, drop=False):
    e = _POOL.get(name)
    if not e:
        return
    e["owner"] = None
    if drop or not e.get("sticky"):
        e["buf"] = None
        e["size"] = 0
        gc.collect()
        gc.collect()


def _alloc_slot(name, size):
    """Try size, then half, then quarter. Returns (buf, actual_size) or (None, 0)."""
    sz = size
    for _ in range(3):
        b = register(name, sz, sticky=True)
        if b is not None:
            return b, sz
        sz = sz // 2
        if sz < 512:
            break
        release(name, drop=True)
    return None, 0


def ensure_capture_slots():
    """
    Dual sticky capture arenas for ping-pong RECORD/SEND.
    Returns (slot0, slot1, slot_size) or (None, None, 0) on hard OOM.
    """
    for n in list(_POOL.keys()):
        if n.startswith("rf_") and n not in ("rf_cap0", "rf_cap1"):
            release(n, True)
    s0, z0 = _alloc_slot("rf_cap0", CAPTURE_SLOT_SIZE)
    if s0 is None:
        return None, None, 0
    s1, z1 = _alloc_slot("rf_cap1", z0)
    if s1 is None:
        release("rf_cap0", True)
        return None, None, 0
    z = z0 if z0 <= z1 else z1
    return s0, s1, z


def ensure_rf_workspace(kind):
    """Small exclusive workspace for non-capture driver needs."""
    for n in list(_POOL.keys()):
        if n.startswith("rf_") and n not in ("rf_cap0", "rf_cap1"):
            release(n, True)
    sizes = {"cc1101": 128, "nrf": 64, "wifi": 64, "ble": 64}
    return register("rf_" + kind, sizes.get(kind, 256), False)


def drop_rf():
    for n in list(_POOL.keys()):
        if n.startswith("rf_"):
            release(n, True)
