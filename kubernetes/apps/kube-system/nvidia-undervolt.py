"""RTX 2070 clock cap + undervolt, run by the nvidia-undervolt DaemonSet (env comes from there).

NVML has no voltage control: a GPC clock VF offset shifts the V/F curve (same clock, lower voltage).
The offset is only stable under the clock cap, so the cap is set first and the offset cleared first.
The driver forgets both on reboot, driver reload or GPU reset, hence the loop.
"""
import ctypes
import os
import signal
import sys
import time

CLOCK_MAX_MHZ = int(os.environ["CLOCK_MAX_MHZ"])
VF_OFFSET_MHZ = int(os.environ["VF_OFFSET_MHZ"])
EXPECT_NAME = os.environ["EXPECT_NAME"]

nvml = ctypes.CDLL("libnvidia-ml.so.1")
gpu = ctypes.c_void_p()


def log(msg):
    print(time.strftime("%Y-%m-%dT%H:%M:%S%z"), msg, flush=True)


def call(fn, *args):
    rc = getattr(nvml, fn)(*args)
    if rc:
        raise RuntimeError(f"{fn} rc={rc}")


def apply():
    if not gpu:
        call("nvmlInit_v2")
        call("nvmlDeviceGetHandleByIndex_v2", 0, ctypes.byref(gpu))
    name = ctypes.create_string_buffer(96)
    call("nvmlDeviceGetName", gpu, name, 96)
    if EXPECT_NAME not in name.value.decode():
        raise RuntimeError(f"unexpected GPU {name.value.decode()!r}, not applying")
    # same as nvidia-smi -lgc 0,<max>; it has no getter, so it is reapplied every loop
    call("nvmlDeviceSetGpuLockedClocks", gpu, 0, CLOCK_MAX_MHZ)
    offset = ctypes.c_int()
    call("nvmlDeviceGetGpcClkVfOffset", gpu, ctypes.byref(offset))
    if offset.value != VF_OFFSET_MHZ:
        call("nvmlDeviceSetGpcClkVfOffset", gpu, VF_OFFSET_MHZ)
        log(f"set cap {CLOCK_MAX_MHZ} MHz, offset +{VF_OFFSET_MHZ} MHz")


def clear(*_):
    try:
        call("nvmlDeviceSetGpcClkVfOffset", gpu, 0)
        call("nvmlDeviceResetGpuLockedClocks", gpu)
        log("cleared offset and clock cap")
    except RuntimeError as e:
        log(f"clear failed: {e}")
    sys.exit(0)


signal.signal(signal.SIGTERM, clear)
while True:
    try:
        apply()
    except RuntimeError as e:
        # Stock is the safe state: log, never exit non-zero, re-init after a driver reload or GPU reset.
        log(f"error: {e}")
        gpu.value = None
    time.sleep(60)
