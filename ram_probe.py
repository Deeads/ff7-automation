"""Read-only discovery of DuckStation's mapped PSX RAM; never writes game memory."""
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import sys

K = C.WinDLL('kernel32', use_last_error=True)

class MBI(C.Structure):
    _fields_ = [('BaseAddress', C.c_void_p), ('AllocationBase', C.c_void_p),
                ('AllocationProtect', W.DWORD), ('PartitionId', W.WORD),
                ('RegionSize', C.c_size_t), ('State', W.DWORD),
                ('Protect', W.DWORD), ('Type', W.DWORD)]

K.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
K.OpenProcess.restype = W.HANDLE
K.CloseHandle.argtypes = [W.HANDLE]
K.VirtualQueryEx.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(MBI), C.c_size_t]
K.VirtualQueryEx.restype = C.c_size_t
K.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
K.ReadProcessMemory.restype = W.BOOL

def read(handle, address, size):
    buffer = C.create_string_buffer(size)
    count = C.c_size_t()
    if not K.ReadProcessMemory(handle, address, buffer, size, C.byref(count)) or count.value != size:
        raise C.WinError(C.get_last_error())
    return buffer.raw

def probe(pid):
    handle = K.OpenProcess(0x410, False, pid)
    if not handle:
        raise C.WinError(C.get_last_error())
    candidates = []
    try:
        address = 0
        while address < 0x7fffffffffff:
            info = MBI()
            if not K.VirtualQueryEx(handle, address, C.byref(info), C.sizeof(info)):
                break
            base = info.BaseAddress or 0
            size = info.RegionSize
            if not size or base + size <= address:
                break
            if info.State == 0x1000 and info.Type == 0x40000 and size in (0x200000, 0x800000) and not info.Protect & 0x101:
                try:
                    data = read(handle, base, 0x200000)
                    path = Path(__file__).parent / f'ram-candidate-{len(candidates)}.bin'
                    path.write_bytes(data)
                    candidates.append({'base': hex(base), 'size': size, 'dump': str(path),
                                       'at_9c6e4': data[0x9c6e4:0x9c6e4+32].hex(),
                                       'at_9c738': data[0x9c738:0x9c738+24].hex()})
                except OSError:
                    pass
            address = base + size
    finally:
        K.CloseHandle(handle)
    print(json.dumps(candidates, indent=2))
    return candidates

if __name__ == '__main__':
    probe(int(sys.argv[1]))
