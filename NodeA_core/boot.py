# boot.py
import gc
import uos

gc.collect()

print("====================================")
print("ColibryOS ESP32-C3 SuperMini")
print("Firmware:", uos.uname())
print("Free memory:", gc.mem_free() // 1024, "KB")
print("====================================")