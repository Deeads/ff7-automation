"""Read-only loader for field event sections straight from a Disc CUE/BIN.

Used to validate `field_script.scan` offline against every field on the disc.
Format: ISO9660 on MODE2/2352 sectors; PSX field .DAT = u32 size + LZSS data,
whose first section (after a 7-pointer table) is the event section.
"""
from pathlib import Path
import re
import struct

SECTOR, DATA = 2352, 24


def lzss(data):
    out = bytearray()
    at = 0
    while at < len(data):
        flags = data[at]; at += 1
        for bit in range(8):
            if at >= len(data):
                break
            if flags >> bit & 1:
                out.append(data[at]); at += 1
            else:
                if at+1 >= len(data):
                    break
                b1, b2 = data[at], data[at+1]; at += 2
                offset, length = b1 | (b2 & 0xF0) << 4, (b2 & 0x0F)+3
                source = len(out)-((len(out)-18-offset) & 0xFFF)
                for i in range(length):
                    out.append(out[source+i] if source+i >= 0 else 0)
    return bytes(out)


class Disc:
    def __init__(self, cue):
        cue = Path(cue)
        match = re.search(r'^\s*FILE\s+"([^"]+)"', cue.read_text(encoding='utf-8-sig'), re.M)
        self.image = open(cue.parent/match.group(1), 'rb')

    def sector(self, lba):
        self.image.seek(lba*SECTOR+DATA)
        return self.image.read(2048)

    def read(self, lba, size):
        return b''.join(self.sector(lba+i) for i in range((size+2047)//2048))[:size]

    def directory(self, lba, size):
        data, entries, at = self.read(lba, size), {}, 0
        while at < len(data):
            length = data[at]
            if not length:
                at = (at//2048+1)*2048
                continue
            extent, bytes_ = struct.unpack_from('<I', data, at+2)[0], struct.unpack_from('<I', data, at+10)[0]
            name = data[at+33:at+33+data[at+32]].decode('ascii', errors='replace').split(';')[0]
            entries[name] = (extent, bytes_, bool(data[at+25] & 2))
            at += length
        return entries

    def listing(self, folder):
        root = self.sector(16)[156:156+34]
        entries = self.directory(struct.unpack_from('<I', root, 2)[0], struct.unpack_from('<I', root, 10)[0])
        lba, size, _ = entries[folder]
        return self.directory(lba, size)

    def event_section(self, lba, size):
        raw = self.read(lba, size)
        data = lzss(raw[4:4+struct.unpack_from('<I', raw)[0]])
        pointers = struct.unpack_from('<7I', data)
        return data[28:28+pointers[1]-pointers[0]]

    def close(self):
        self.image.close()
