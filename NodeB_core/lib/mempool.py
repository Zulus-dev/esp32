# lib/mempool.py — tiny named slabs for Node B RF workspaces.
import gc

_POOL = {}

def register(name, size, sticky=False):
    e = _POOL.get(name)
    if e and e["buf"] is not None and e["size"] >= size:
        return e["buf"]
    gc.collect()
    b = bytearray(size)
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
        gc.collect(); gc.collect()

def ensure_rf_workspace(kind):
    for n in list(_POOL.keys()):
        if n.startswith("rf_"):
            release(n, True)
    sizes = {"cc1101": 128, "nrf": 64, "wifi": 64, "ble": 64}
    return register("rf_" + kind, sizes.get(kind, 256), False)

def drop_rf():
    for n in list(_POOL.keys()):
        if n.startswith("rf_"):
            release(n, True)
