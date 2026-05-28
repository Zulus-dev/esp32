# core/loader.py - deprecated compatibility shim
import gc
import sys


class ModuleLoader:
    @staticmethod
    def load(module_path):
        try:
            return __import__(module_path, None, None, ("*",))
        except Exception as exc:
            print("[LOAD FAIL]", module_path, exc)
            return None

    @staticmethod
    def purge(module_path):
        prefix = module_path + "."
        for name in tuple(sys.modules.keys()):
            if name == module_path or name.startswith(prefix):
                try:
                    del sys.modules[name]
                except KeyError:
                    pass
        gc.collect()
