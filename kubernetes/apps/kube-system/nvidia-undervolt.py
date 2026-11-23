"""RTX 2070 clock cap + undervolt, run by the nvidia-undervolt DaemonSet (env comes from there).

NVML has no voltage control: a GPC clock VF offset shifts the V/F curve (same clock, lower voltage),
and the offset is only stable together with the clock cap (+300 faulted with the cap lifted to 1560).
So the cap is applied first and the offset removed first. Stock is the safe state: any NVML error or an
Xid logs and keeps going, and the process never exits non-zero (no crash loop).
Settings live in the driver and reset on reboot, driver reload or GPU reset, hence the loop.
"""
import ctypes
import os
import signal
import sys
import time

CLOCK_MAX_MHZ = int(os.environ["CLOCK_MAX_MHZ"])
VF_OFFSET_MHZ = int(os.environ["VF_OFFSET_MHZ"])
EXPECT_NAME = os.environ["EXPECT_NAME"]
INTERVAL_S = int(os.environ.get("INTERVAL_S", "60"))


def log(msg):
    print(time.strftime("%Y-%m-%dT%H:%M:%S%z"), msg, flush=True)


nvml = ctypes.CDLL("libnvidia-ml.so.1")
handle = ctypes.c_void_p()


def call(fn, *args):
    rc = getattr(nvml, fn)(*args)
    if rc != 0:
        raise RuntimeError(f"{fn} rc={rc}")


def init():
    call("nvmlInit_v2")
    call("nvmlDeviceGetHandleByIndex_v2", 0, ctypes.byref(handle))
    name = ctypes.create_string_buffer(96)
    call("nvmlDeviceGetName", handle, name, 96)
    if EXPECT_NAME not in name.value.decode():
        raise RuntimeError(f"unexpected GPU {name.value.decode()!r}, not applying")
    lo, hi = ctypes.c_int(), ctypes.c_int()
    call("nvmlDeviceGetGpcClkMinMaxVfOffset", handle, ctypes.byref(lo), ctypes.byref(hi))
    if not lo.value <= VF_OFFSET_MHZ <= hi.value:
        raise RuntimeError(f"offset {VF_OFFSET_MHZ} outside NVML range {lo.value}..{hi.value}")


def get_offset():
    v = ctypes.c_int()
    call("nvmlDeviceGetGpcClkVfOffset", handle, ctypes.byref(v))
    return v.value


def apply_undervolt():
    # same as nvidia-smi -lgc 0,<max>; no getter exists, so it is simply reapplied each loop
    call("nvmlDeviceSetGpuLockedClocks", handle, 0, CLOCK_MAX_MHZ)
    if get_offset() != VF_OFFSET_MHZ:
        call("nvmlDeviceSetGpcClkVfOffset", handle, VF_OFFSET_MHZ)
        log(f"set cap {CLOCK_MAX_MHZ} MHz, offset +{VF_OFFSET_MHZ} MHz")


def clear():
    try:
        if get_offset() != 0:
            call("nvmlDeviceSetGpcClkVfOffset", handle, 0)
        call("nvmlDeviceResetGpuLockedClocks", handle)
        log("cleared offset and clock cap")
    except Exception as e:  # noqa: BLE001
        log(f"clear failed: {e}")


def stop(*_):
    clear()
    sys.exit(0)


# /dev/kmsg: one record per read, "pri,seq,ts,flags;message". Xids from before this start are ignored
# (tests, earlier crashes); a restart re-arms the offset, so look at the Xid before restarting the pod.
kmsg = os.open("/dev/kmsg", os.O_RDONLY | os.O_NONBLOCK)


def drain():
    recs = []
    while True:
        try:
            recs.append(os.read(kmsg, 8192).decode(errors="replace"))
        except BlockingIOError:
            return recs
        except OSError:  # buffer overrun: records were lost, keep reading
            continue


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
drain()
latched = False
while True:
    try:
        if not handle:
            init()
        xids = [r for r in drain() if "NVRM: Xid" in r]
        if xids and not latched:
            latched = True
            log(f"Xid seen: {xids[0].split(';', 1)[-1].strip()}")
            clear()
            log("latched to stock until the pod restarts: investigate the Xid first")
        if not latched:
            apply_undervolt()
    except Exception as e:  # noqa: BLE001
        log(f"error: {e}")
        handle = ctypes.c_void_p()  # re-init after a driver reload or GPU reset
    time.sleep(INTERVAL_S)
