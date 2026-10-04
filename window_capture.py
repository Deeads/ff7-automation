"""Capture the DuckStation window image without focusing or moving it.

Uses PrintWindow(PW_RENDERFULLCONTENT) on the emulator's top-level window, so
it works while you use other apps. Output is a plain PNG (numpy + zlib only).
Read-only: no input is sent and no window state changes.
"""
import ctypes
from ctypes import wintypes as W
import struct
import sys
import zlib

import numpy

user32, gdi32 = ctypes.WinDLL('user32'), ctypes.WinDLL('gdi32')
PW_RENDERFULLCONTENT = 2
user32.GetDC.restype = W.HDC
user32.GetDC.argtypes = [W.HWND]
user32.ReleaseDC.argtypes = [W.HWND, W.HDC]
user32.PrintWindow.argtypes = [W.HWND, W.HDC, W.UINT]
user32.GetClientRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
user32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
user32.IsWindowVisible.argtypes = [W.HWND]
user32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
gdi32.CreateCompatibleDC.restype = W.HDC
gdi32.CreateCompatibleDC.argtypes = [W.HDC]
gdi32.CreateCompatibleBitmap.restype = W.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = W.HGDIOBJ
gdi32.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]
gdi32.DeleteObject.argtypes = [W.HGDIOBJ]
gdi32.DeleteDC.argtypes = [W.HDC]
gdi32.GetDIBits.argtypes = [W.HDC, W.HBITMAP, W.UINT, W.UINT, ctypes.c_void_p, ctypes.c_void_p, W.UINT]


class BitmapHeader(ctypes.Structure):
    _fields_ = [('size', W.DWORD), ('width', W.LONG), ('height', W.LONG), ('planes', W.WORD),
                ('bits', W.WORD), ('compression', W.DWORD), ('image', W.DWORD), ('x', W.LONG),
                ('y', W.LONG), ('used', W.DWORD), ('important', W.DWORD)]


def windows_of(pid):
    found = []
    proc = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    def visit(hwnd, _):
        owner = W.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            rect = W.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            # The render window is titled with the game; the list window is "DuckStation <version>".
            game = bool(title.value) and not title.value.startswith('DuckStation')
            found.append((game, rect.right*rect.bottom, hwnd, rect.right, rect.bottom))
        return True
    user32.EnumWindows(proc(visit), 0)
    return sorted(found, reverse=True)


def capture(hwnd, width, height):
    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    old = gdi32.SelectObject(memory, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory, PW_RENDERFULLCONTENT):
            raise OSError('PrintWindow failed.')
        header = BitmapHeader(ctypes.sizeof(BitmapHeader), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        buffer = ctypes.create_string_buffer(width*height*4)
        if not gdi32.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(header), 0):
            raise OSError('GetDIBits failed.')
        return buffer.raw
    finally:
        gdi32.SelectObject(memory, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, screen)


def png(bgra, width, height):
    pixels = numpy.frombuffer(bgra, numpy.uint8).reshape(height, width, 4)[:, :, 2::-1]
    rows = numpy.concatenate([numpy.zeros((height, 1), numpy.uint8), pixels.reshape(height, width*3)], axis=1)
    chunk = lambda kind, data: struct.pack('>I', len(data))+kind+data+struct.pack('>I', zlib.crc32(kind+data))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows.tobytes(), 6))+chunk(b'IEND', b''))


def save(pid, path):
    windows = windows_of(pid)
    if not windows:
        raise OSError('No visible emulator window.')
    _, _, hwnd, width, height = windows[0]
    with open(path, 'wb') as stream:
        stream.write(png(capture(hwnd, width, height), width, height))
    return width, height


if __name__ == '__main__':
    from ff7_state import find_duckstation
    print(save(find_duckstation(), sys.argv[1] if len(sys.argv) > 1 else 'capture.png'))
