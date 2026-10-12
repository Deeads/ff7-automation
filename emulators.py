"""Windows process/RAM discovery. Only QUERY_INFORMATION and VM_READ are used.

Other emulator names are discovery candidates, not claims of tested support.
Automatic discovery requires contiguous, page-aligned, little-endian PS1 RAM.
An explicit base can address an unaligned allocation; the FF7 hash still gates it.
"""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import time

from ram_probe import K, MBI, read

RAM_SIZE = 0x200000
KERNEL_OFFSET = 0x44000
KERNEL_SIZE = 0x100
KERNEL_HASH = '2e9e460274da2383970500a7490bab98f5652752b67dbea630c3e72c9b460106'
EMULATORS = {
    'duckstation': ('duckstation',),
    'retroarch': ('retroarch',),
    'bizhawk': ('emuhawk',),
    'mednafen': ('mednafen',),
    'epsxe': ('epsxe',),
    'pcsx': ('pcsx',),
}


class StateError(RuntimeError):
    pass


def emulator_name(exe):
    name = exe.lower().removesuffix('.exe')
    for family, prefixes in EMULATORS.items():
        if any(name == p or name.startswith(p + '-') for p in prefixes):
            return family
    return None


def processes():
    class Entry(C.Structure):
        _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('pid', W.DWORD),
                    ('heap', C.c_size_t), ('module', W.DWORD), ('threads', W.DWORD),
                    ('parent', W.DWORD), ('priority', W.LONG), ('flags', W.DWORD),
                    ('exe', W.WCHAR * 260)]
    K.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    K.CreateToolhelp32Snapshot.restype = W.HANDLE
    K.Process32FirstW.argtypes = K.Process32NextW.argtypes = [W.HANDLE, C.POINTER(Entry)]
    snapshot = K.CreateToolhelp32Snapshot(2, 0)
    if snapshot == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    found = []
    try:
        entry = Entry()
        entry.size = C.sizeof(entry)
        more = K.Process32FirstW(snapshot, C.byref(entry))
        while more:
            family = emulator_name(entry.exe)
            if family:
                found.append(dict(pid=entry.pid, name=entry.exe, emulator=family))
            more = K.Process32NextW(snapshot, C.byref(entry))
    finally:
        K.CloseHandle(snapshot)
    return found


def select_process(candidates, emulator='auto', pid=None):
    if pid is not None:
        if pid <= 0:
            raise StateError('PID must be positive.')
        return next((p for p in candidates if p['pid'] == pid),
                    dict(pid=pid, name='explicit PID', emulator='custom'))
    candidates = [p for p in candidates if emulator == 'auto' or p['emulator'] == emulator]
    if not candidates:
        raise StateError(f'No {emulator if emulator != "auto" else "PS1 emulator"} process found. Start the emulator and load FF7 USA.')
    if len(candidates) != 1:
        names = ', '.join(f'{p["name"]} (PID {p["pid"]})' for p in candidates)
        raise StateError(f'Multiple emulators found: {names}. Close the extra instances or select --pid for observation.')
    return candidates[0]


def regions(handle):
    address = 0
    while address < 0x7FFFFFFFFFFF:
        info = MBI()
        if not K.VirtualQueryEx(handle, address, C.byref(info), C.sizeof(info)):
            break
        base, size = info.BaseAddress or 0, info.RegionSize
        if not size or base + size <= address:
            break
        # Readable committed pages; exclude guard, no-access, execute-only.
        if info.State == 0x1000 and info.Protect & 0xFF in (2, 4, 8, 0x20, 0x40, 0x80) and not info.Protect & 0x100:
            yield base, size
        address = base + size


def verified(data):
    return len(data) == KERNEL_SIZE and hashlib.sha256(data).hexdigest() == KERNEL_HASH


def find_ram(handle, memory_regions, *, base=None, stopped=lambda: False,
             reader=read, max_scan_bytes=512 * 1024 * 1024):
    """Fast mapping probe, then bounded page scan of larger heap allocations.

    Do not scan unrelated processes, and do not accept a hash at an address
    whose full 2 MB allocation is unreadable. Multiple copies stay ambiguous.
    """
    if base is not None:
        if base < 0 or not verified(reader(handle, base + KERNEL_OFFSET, KERNEL_SIZE)):
            raise StateError('Explicit RAM base does not contain the verified FF7 USA executable.')
        reader(handle, base, RAM_SIZE)
        return [base]
    memory_regions = [(b, n) for b, n in memory_regions if n >= RAM_SIZE]
    found = []
    for candidate, size in memory_regions:
        if stopped():
            raise StateError('RAM discovery stopped.')
        try:
            if verified(reader(handle, candidate + KERNEL_OFFSET, KERNEL_SIZE)):
                reader(handle, candidate, RAM_SIZE)
                found.append(candidate)
        except OSError:
            pass
    if found:
        return found
    # Smaller allocations first: emulated RAM usually precedes giant JIT arenas.
    scanned = 0
    for start, size in sorted(memory_regions, key=lambda r: r[1]):
        for offset in range(0x1000, size - RAM_SIZE + 1, 0x1000):
            if stopped():
                raise StateError('RAM discovery stopped.')
            scanned += 0x1000
            if scanned > max_scan_bytes:
                raise StateError('RAM scan budget exceeded. Use --ram-base with the emulator debugger RAM address.')
            candidate = start + offset
            try:
                if verified(reader(handle, candidate + KERNEL_OFFSET, KERNEL_SIZE)):
                    reader(handle, candidate, RAM_SIZE)
                    found.append(candidate)
            except OSError:
                continue
    return found


def live_mappings(handle, bases, reader=read, sleep=time.sleep):
    first = {b: hashlib.sha256(reader(handle, b, RAM_SIZE)).digest() for b in bases}
    if len(set(first.values())) == 1:
        # DuckStation exposes identical shared-memory mirrors.
        return bases[:1]
    sleep(0.15)
    changing = [b for b in bases if hashlib.sha256(reader(handle, b, RAM_SIZE)).digest() != first[b]]
    return changing or bases
