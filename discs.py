"""Validate owned FF7 images and prepare an ordered DuckStation playlist.

Preparation never changes the running emulator or silently replaces a disc.
The controller hotkey uses unbound thumb buttons, leaving normal gameplay alone.
"""
import argparse
import json
from pathlib import Path
import re
import struct

SERIALS = {1: 'SCUS_941.63', 2: 'SCUS_941.64', 3: 'SCUS_941.65'}
NEXT_DISC_BINDING = 'SDL-0/LeftStick & SDL-0/RightStick'


def image_serial(path):
    path = Path(path).resolve(strict=True)
    source = path
    if path.suffix.lower() == '.cue':
        cue = path.read_text(encoding='utf-8-sig')
        match = re.search(r'^\s*FILE\s+"([^"]+)"\s+BINARY\s*$', cue, re.I|re.M)
        if not match:
            raise ValueError('CUE has no supported binary data track.')
        source = (path.parent/match[1]).resolve(strict=True)
        if not re.search(r'TRACK\s+01\s+MODE[12]/2352', cue, re.I):
            raise ValueError('Expected a MODE1/2352 or MODE2/2352 data track.')
        first_track = re.split(r'TRACK\s+02', cue, flags=re.I)[0]
        if not re.search(r'INDEX\s+01\s+00:00:00', first_track, re.I):
            raise ValueError('Data tracks with a nonzero start offset are not supported.')
    elif path.suffix.lower() not in ('.bin', '.iso'):
        raise ValueError('Image validation currently supports CUE/BIN and ISO.')
    with source.open('rb') as stream:
        geometry = None
        for sector_size, offset in ((2352,24),(2352,16),(2048,0)):
            stream.seek(16*sector_size+offset)
            pvd = stream.read(2048)
            if pvd[:7] == b'\x01CD001\x01':
                if len(pvd) != 2048:
                    raise ValueError('Truncated disc volume descriptor.')
                geometry = sector_size,offset
                break
        if geometry is None:
            raise ValueError('No supported ISO9660 primary volume descriptor.')
        sector_size, offset = geometry
        def extent(lba, size):
            if not 0 <= size <= 1024*1024:
                raise ValueError('Implausible disc metadata length.')
            output=bytearray()
            for index in range((size+2047)//2048):
                stream.seek((lba+index)*sector_size+offset)
                chunk=stream.read(2048)
                if len(chunk)!=2048:
                    raise ValueError('Truncated disc image.')
                output.extend(chunk)
            return bytes(output[:size])
        directory=extent(struct.unpack_from('<I',pvd,158)[0],struct.unpack_from('<I',pvd,166)[0])
        position=0
        while position<len(directory):
            length=directory[position]
            if not length:
                position=(position//2048+1)*2048
                continue
            record=directory[position:position+length]
            if len(record)<34 or len(record)<33+record[32]:
                raise ValueError('Invalid disc directory record.')
            name=record[33:33+record[32]].decode('ascii',errors='replace').split(';')[0]
            if name.upper()=='SYSTEM.CNF':
                config=extent(struct.unpack_from('<I',record,2)[0],struct.unpack_from('<I',record,10)[0])
                boot=re.search(rb'SCUS[_-]941[.]?(6[345])',config,re.I)
                if not boot:
                    raise ValueError('Disc is not the expected FF7 USA release.')
                return 'SCUS_941.'+boot[1].decode()
            position+=length
    raise ValueError('SYSTEM.CNF not found on the disc.')


def prepare(paths, output):
    paths = list(paths)
    if len(paths) != 3:
        raise ValueError('All three disc images are required for the complete playlist.')
    output=Path(output)
    ordered=[]
    for number,path in enumerate(paths,1):
        path=Path(path).resolve(strict=True)
        if '\n' in str(path) or '\r' in str(path):
            raise ValueError('Invalid playlist path.')
        serial=image_serial(path)
        if serial!=SERIALS[number]:
            raise ValueError(f'Expected Disc {number} ({SERIALS[number]}), got {serial}.')
        ordered.append(dict(disc=number,path=str(path),serial=serial))
    if len(ordered)!=3:
        raise ValueError('All three disc images are required for the complete playlist.')
    output.mkdir(parents=True,exist_ok=True)
    playlist=output/'Final Fantasy VII.m3u'
    playlist.write_text('\n'.join(d['path'] for d in ordered)+'\n',encoding='utf-8')
    manifest=dict(discs=ordered,playlist=str(playlist),next_disc_hotkey=NEXT_DISC_BINDING,
                  runtime_ready=False,reason='Load this playlist and configure SwitchToNextDisc before enabling swaps.')
    (output/'discs.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images',nargs='+',type=Path)
    parser.add_argument('--output',type=Path,default=Path(__file__).parent)
    parser.add_argument('--inspect',action='store_true')
    args=parser.parse_args()
    if args.inspect:
        print(json.dumps([dict(path=str(p),serial=image_serial(p)) for p in args.images],indent=2))
    else:
        print(json.dumps(prepare(args.images,args.output),indent=2))
