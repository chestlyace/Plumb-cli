"""What the machine can run."""

import ctypes
import os
import sys

SMALL_MODEL = "gemma4:e2b"
LARGER_MODEL = "gemma4:e4b"
MODEL_SIZES = {SMALL_MODEL: "4.6 GB", LARGER_MODEL: "6.6 GB"}
# At or below this much RAM, the smaller model leaves room for an editor and a browser.
SMALL_MODEL_MAX_RAM_GB = 8.5


def total_ram_gb() -> float | None:
    """Total physical memory in GB, or None if it can't be read."""
    try:
        if sys.platform == "win32":

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return None
            total = status.ullTotalPhys
        else:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except AttributeError, OSError, ValueError:
        return None
    return total / 1024**3


def recommend_model(ram_gb: float | None) -> str:
    if ram_gb is not None and ram_gb <= SMALL_MODEL_MAX_RAM_GB:
        return SMALL_MODEL
    return LARGER_MODEL
