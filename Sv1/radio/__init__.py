# radio/__init__.py — lazy loader
import gc
import sys

def load_module(name):
    try:
        return __import__(f"radio.{name}", None, None, ["*"])
    except Exception as e:
        print(f"[RADIO LOAD FAIL] {name}: {e}")
        return None

def purge_module(name):
    prefix = f"radio.{name}"
    for k in list(sys.modules.keys()):
        if k == prefix or k.startswith(prefix + "."):
            del sys.modules[k]
    gc.collect()