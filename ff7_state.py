"""Read-only DuckStation adapter for the verified SCUS-94163 FF7 build.

Offsets and field structures: q-gears/q-gears-reversing-data, and
https://ff7-mods.github.io/ff7-flat-wiki/FF7/Savemap.html
Unknown modes/invalid pointers are errors, never guessed state.
"""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import math
import struct
import time

from ram_probe import K, MBI, read
from battle import decode_battle
from field_script import field_checks, field_triggers, BYTE_BANKS
import battle_menu

RAM_SIZE = 0x200000
SAVE = 0x9C6E4
GAME = 0x9ABF4
TEMP_BANK = 0x75E24  # field temp bank 5/6 (reference/ffvii_field_ffvii_address.txt)
DIRECTION_ANGLES = dict(up=0, up_left=32, left=64, down_left=96,
                        down=128, down_right=160, right=192, up_right=224)
KERNEL_HASH = '2e9e460274da2383970500a7490bab98f5652752b67dbea630c3e72c9b460106'


class StateError(RuntimeError):
    pass


def fftext(data):
    result = []
    for value in data:
        if value == 255:
            break
        punctuation = {0xA9: '...', 0xAA: ' ', 0xB0: '-', 0xB1: '-', 0xB2: '"', 0xB3: '"', 0xB4: "'", 0xB5: "'", 0xE7: '\n'}
        result.append(chr(value + 32) if value < 95 else punctuation.get(value, '?'))
    return ''.join(result)


def choice_row(pointer_y):
    return (pointer_y-6)//16 if pointer_y >= 6 and (pointer_y-6) % 16 == 0 else None


def decode(data):
    if len(data) != RAM_SIZE or hashlib.sha256(data[0x44000:0x44100]).hexdigest() != KERNEL_HASH:
        raise StateError('RAM does not match the verified FF7 USA executable.')
    u16 = lambda p: struct.unpack_from('<H', data, p)[0]
    s16 = lambda p: struct.unpack_from('<h', data, p)[0]
    u32 = lambda p: struct.unpack_from('<I', data, p)[0]

    def ptr(at, length=1):
        address = u32(at)
        if not 0x80000000 <= address <= 0x80200000 - length:
            raise StateError(f'Invalid game pointer at {at:#x}')
        return address - 0x80000000

    party = []
    for char_id in data[SAVE + 0x4F8:SAVE + 0x4FB]:
        if char_id == 255:
            continue
        if char_id > 10:
            raise StateError('Invalid party record.')
        record = SAVE + 0x54 + {9: 6, 10: 7}.get(char_id, char_id) * 132
        level = data[record + 1]
        hp, mp = u16(record + 0x2C), u16(record + 0x30)
        max_hp, max_mp = u16(record + 0x38), u16(record + 0x3A)
        if not 1 <= level <= 99 or not 1 <= max_hp <= 9999 or hp > max_hp or mp > max_mp:
            raise StateError('Uninitialized or invalid character record.')
        materia = [data[record+at] for at in range(0x40, 0x80, 4) if data[record+at] != 0xFF]
        party.append(dict(id=char_id, name=fftext(data[record+0x10:record+0x1C]),
                          level=level, hp=hp, max_hp=max_hp, mp=mp, max_mp=max_mp,
                          limit_bar=data[record+0x0F], materia=materia))
    if not party:
        raise StateError('No loaded party.')
    state = dict(schema=1, captured_monotonic=time.monotonic(), game='FF7-USA-verified',
                 mode='unsupported', field_id=u16(0x9A05C),
                 field_state=data[GAME+1], module_request=u16(0x9C560),
                 story_progress=u16(SAVE+0xBA4), gil=u32(SAVE+0xB7C), party=party,
                 health_source='persistent party records; not battle combatant HP',
                 player=None, nearby_actors=[], dialogs=[], navigation=None)
    state['key_items'] = list(data[SAVE+0xBE4:SAVE+0xBEC])
    # Item stock: u16 per slot, id in bits 0-8, quantity in bits 9-15 (savemap 0x4FC).
    items = {}
    for slot in range(320):
        value = u16(SAVE+0x4FC+slot*2)
        if value != 0xFFFF:
            items[value & 0x1FF] = items.get(value & 0x1FF, 0)+(value >> 9)
    state['items'] = items
    state['required_disc'] = data[SAVE+0xEA4]
    state['puzzles'] = {'domino_password': {1: 'BEST', 2: 'BOMB', 4: 'MAKO', 8: 'KING'}.get(data[SAVE+0xD5A] & 15)}
    state['puzzles'].update(midgar_placed=data[SAVE+0xD58]&0xF8,
                            shinra_chests=list(data[SAVE+0xBDC:SAVE+0xBDE]))
    # Raw field-script variable banks for the route table's conditions.
    # 1/3/11/13/15 live in the savemap; 5 is the field's temporary bank.
    state['banks'] = {str(bank): data[SAVE+offset:SAVE+offset+256].hex() for bank, offset in BYTE_BANKS.items()}
    state['banks']['5'] = data[TEMP_BANK:TEMP_BANK+256].hex()
    if state['module_request'] == 2:
        state['mode'] = 'battle_loading'
        state['battle'] = decode_battle(data)
        if state['battle']:
            state['mode'] = 'battle'
            menu_map = battle_menu.load_map()
            if menu_map:
                state['battle']['menu'] = battle_menu.read_menu(data, menu_map)
        return state
    if ptr(0x9C6E0, 0x100) != GAME or ptr(0x9C544, 132) != 0x74EA4:
        raise StateError('Field structure pointers changed; unsupported mode/build.')
    # These are field requests, not a universal current-module enum. Only the
    # observed main-menu and normal-field cases are enabled by this adapter.
    if state['field_state'] == 9 and state['module_request'] == 5:
        state['mode'] = 'menu'
        return state
    if state['field_state'] != 0 or state['module_request'] != 1:
        return state
    script = ptr(0x9C6DC, 32)
    entity_count = data[script+2]
    if not 1 <= entity_count <= 64 or script+32+entity_count*8 > RAM_SIZE:
        raise StateError('Invalid field script header.')
    entity_names = [data[script+32+i*8:script+40+i*8].split(b'\0')[0].decode('ascii', errors='replace') for i in range(entity_count)]
    count, player_id = u16(GAME+0x28), s16(0x965E0)
    if not 1 <= count <= 16 or not 0 <= player_id < count:
        raise StateError('Invalid live field model list.')

    def actor(index):
        at = 0x74EA4 + index * 132
        xyz = [round(n / 4096, 3) for n in struct.unpack_from('<iii', data, at+12)]
        return dict(id=index, position=xyz, triangle=u16(at+0x72), entity=data[at+0x57],
                    entity_name=entity_names[data[at+0x57]] if data[at+0x57] < entity_count else None,
                    facing=data[at+0x38],
                    visible=bool(data[at+0x5C]), solid=data[at+0x59] == 0,
                    talkable=data[at+0x5B] == 0, talk_radius=u16(at+0x6E),
                    collision_radius=u16(at+0x6C), animation_state=data[at+0x5D])

    # Chests, pickups and save points from the loaded field's own scripts
    # (read-only disassembly; cached per event section).
    # PHS visibility mask (savemap 0x10A6): who has joined. Verified live: 0xF = Cloud..Aeris.
    state['phs_visible'] = struct.unpack_from('<H', data, SAVE+0x10A6)[0]
    state['field_checks'] = field_checks(data, script)
    state['field_triggers'] = field_triggers(data, script)
    player = actor(player_id)
    state['player'] = player
    # Model state 4/5 is a player-controlled LADER (ladder/duct): the game
    # reads the pad every frame there, so it must not count as a script lock.
    # (ffvii_field_movement_move_update.cpp, "ladder update".)
    ladder_state = player['animation_state']
    if ladder_state in (4, 5):
        at = 0x74EA4 + player_id*132
        switch = s16(at+0x68)
        # Which pad direction advances. Verified live on blin66_4 (state 4,
        # switch 0): D-pad Up backed out to the vent, Down crawled forward.
        # The reference's key names read the other way round; trust the live test.
        # State 5 (sideways) follows the same pattern but is not yet live-verified.
        forward = {(4, 0): 'down', (4, 1): 'up', (5, 0): 'left', (5, 1): 'right'}.get((ladder_state, 1 if switch else 0))
        player['ladder'] = dict(forward=forward, step=s16(at+0x32), steps=s16(at+0x30),
                                destination=[round(n/4096) for n in struct.unpack_from('<iii', data, at+0x78)])
    state['movement_locked'] = bool(data[GAME+0x32]) or (bool(ladder_state) and ladder_state not in (4, 5))
    state['fading'] = u16(GAME+0x38) != 0
    state['mode'] = 'field'
    actors = [actor(i) for i in range(count) if i != player_id]
    for item in actors:
        item['distance'] = round(math.dist(item['position'], player['position']), 1)
    state['nearby_actors'] = sorted([a for a in actors if a['visible']], key=lambda a: a['distance'])[:6]
    state['actors'] = [a for a in actors if a['visible']]
    for index in range(4):
        at = 0x83274 + index * 0x30
        status = u16(at+0x2C)
        if status == 0:
            continue
        if status > 14:
            raise StateError('Invalid dialogue state.')
        text_ptr = ptr(at, 256)
        state['dialogs'].append(dict(id=index, state=status,
            choice_pointer=bool(data[at+0x1A]), text=fftext(data[text_ptr:text_ptr+256]),
            choice_row=choice_row(u16(at+0x26)) if data[at+0x1A] else None,
            ready=status in (4, 6, 11, 13, 14), auto_close=bool(data[at+0x2E] & 1)))
    if state['dialogs']:
        state['mode'] = 'dialog'
    # Local geometry is useful context, not a complete pathfinder. Keep Z and
    # adjacency: projecting an entire multi-floor field to XY loses obstacles.
    mesh = ptr(0xE4274, 24)
    total = u16(mesh-4)
    adjacency = ptr(0x114458, 6)
    tri = player['triangle']
    if not 1 <= total <= 4096 or tri >= total or mesh+24*total > RAM_SIZE or adjacency+6*total > RAM_SIZE:
        raise StateError('Invalid walkmesh.')
    neighbors = list(struct.unpack_from('<3h', data, adjacency+tri*6))
    triangles = []
    for index in [tri] + [n for n in neighbors if 0 <= n < total]:
        at = mesh + index * 24
        triangles.append(dict(id=index, vertices=[list(struct.unpack_from('<hhh', data, at+j*8)) for j in range(3)]))
    trig = ptr(0x716C4, 10)
    shift = data[trig+9] + data[0x74EA4+player_id*132+0x35]
    vectors = {}
    for name, angle in DIRECTION_ANGLES.items():
        x, y = struct.unpack_from('<hh', data, 0xDF120 + ((angle+shift)&255)*4)
        vectors[name] = [round(x/4096, 4), round(-y/4096, 4)]
    state['navigation'] = dict(triangle_count=total, neighbors=neighbors,
                               local_triangles=triangles, direction_vectors=vectors)
    state['navigation']['direction_angles'] = {name: (angle+shift)&255 for name, angle in DIRECTION_ANGLES.items()}
    # A full adjacency graph is needed to route around walls and through doors.
    graph = []
    if total > 512:
        raise StateError('Walkmesh exceeds the documented 512-bit triangle lock array.')
    for index in range(total):
        at = mesh+index*24
        edges = list(struct.unpack_from('<3h', data, adjacency+index*6))
        if any(n < -1 or n >= total for n in edges):
            raise StateError('Walkmesh adjacency outside triangle array.')
        graph.append(dict(id=index, vertices=[list(struct.unpack_from('<hhh', data, at+j*8)) for j in range(3)],
                          neighbors=edges, locked=bool(data[GAME+0xB2+index//8] & (1 << (index%8)))))
    state['navigation']['mesh'] = graph
    state['field_name'] = data[trig:trig+9].split(b'\0')[0].decode('ascii', errors='replace')
    state['gateways'] = []
    for index in range(12):
        at = trig+56+index*24
        destination = u16(at+18)
        if destination == 32767:
            continue
        if destination > 1000:
            raise StateError('Invalid gateway destination.')
        state['gateways'].append(dict(id=index, destination=destination,
            line=[list(struct.unpack_from('<hhh', data, at+j*6)) for j in range(2)],
            destination_position=list(struct.unpack_from('<hhh', data, at+12))))
    return state


def party_health(data):
    """(name, HP, max HP) of the party from the save records; readable on
    screens (results, game over) where decode() refuses the field layout."""
    members = []
    for char_id in data[SAVE+0x4F8:SAVE+0x4FB]:
        if char_id <= 10:
            record = SAVE+0x54+{9: 6, 10: 7}.get(char_id, char_id)*132
            members.append((fftext(data[record+0x10:record+0x1C]),
                            struct.unpack_from('<H', data, record+0x2C)[0],
                            struct.unpack_from('<H', data, record+0x38)[0]))
    return members


def party_hp(data):
    return [hp for _, hp, _ in party_health(data)]


def find_duckstation():
    class Entry(C.Structure):
        _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('pid', W.DWORD),
                    ('heap', C.c_size_t), ('module', W.DWORD), ('threads', W.DWORD),
                    ('parent', W.DWORD), ('priority', W.LONG), ('flags', W.DWORD),
                    ('exe', W.WCHAR*260)]
    K.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    K.CreateToolhelp32Snapshot.restype = W.HANDLE
    K.Process32FirstW.argtypes = K.Process32NextW.argtypes = [W.HANDLE, C.POINTER(Entry)]
    snapshot = K.CreateToolhelp32Snapshot(2, 0)
    if snapshot == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    pids = []
    try:
        entry = Entry(); entry.size = C.sizeof(entry)
        more = K.Process32FirstW(snapshot, C.byref(entry))
        while more:
            if entry.exe.lower().startswith('duckstation') and entry.exe.lower().endswith('.exe'):
                pids.append(entry.pid)
            more = K.Process32NextW(snapshot, C.byref(entry))
    finally:
        K.CloseHandle(snapshot)
    if len(pids) != 1:
        raise StateError(f'Expected one DuckStation process; found {len(pids)}. Use --pid when needed.')
    return pids[0]


class LiveRAM:
    def __init__(self, pid=None):
        self.pid = pid or find_duckstation()
        self.handle = K.OpenProcess(0x410, False, self.pid)  # QUERY_INFORMATION | VM_READ only
        if not self.handle:
            raise C.WinError(C.get_last_error())
        try:
            found = []
            address = 0
            while address < 0x7FFFFFFFFFFF:
                info = MBI()
                if not K.VirtualQueryEx(self.handle, address, C.byref(info), C.sizeof(info)):
                    break
                base, size = info.BaseAddress or 0, info.RegionSize
                if not size or base+size <= address:
                    break
                if info.State == 0x1000 and info.Type == 0x40000 and size in (RAM_SIZE, 4*RAM_SIZE) and not info.Protect & 0x101:
                    # Identify FF7 by its executable only: results, game-over and
                    # transition screens are still FF7 even though decode() refuses them.
                    try:
                        if hashlib.sha256(read(self.handle, base+0x44000, 0x100)).hexdigest() == KERNEL_HASH:
                            found.append(base)
                    except OSError:
                        pass
                address = base+size
            if len(found) > 1:
                found = self._live_mappings(found)
            if len(found) != 1:
                raise StateError(f'Expected one verified FF7 RAM mapping; found {len(found)}.')
            self.base = found[0]
        except BaseException:
            self.close()
            raise

    def _live_mappings(self, bases):
        """DuckStation can expose more than one mapping holding the FF7 RAM.
        Seen live: two adjacent 8 MB views with identical bytes (mirrors of the
        same memory), so either is correct. If copies differ (e.g. a frozen
        rewind/save-state buffer), keep the one that changes between two reads."""
        first = {b: hashlib.sha256(read(self.handle, b, RAM_SIZE)).digest() for b in bases}
        if len(set(first.values())) == 1:
            return bases[:1]  # identical mirror views of the same RAM (seen live): any one is correct
        time.sleep(0.15)
        changing = [b for b in bases if hashlib.sha256(read(self.handle, b, RAM_SIZE)).digest() != first[b]]
        return changing or bases

    def raw(self):
        return read(self.handle, self.base, RAM_SIZE)

    def snapshot(self, attempts=100):
        # Field loads update several pointers over different frames. Retrying
        # read-only snapshots avoids treating that ordinary transition as a
        # permanently invalid layout; no input is held during these retries.
        last_error = None
        for attempt in range(attempts):
            data = read(self.handle, self.base, RAM_SIZE)
            try:
                for at, size in ((GAME+1, 1), (0x9A05C, 2), (0x9C560, 2), (0x9C6DC, 8), (0xE4274, 4)):
                    if read(self.handle, self.base+at, size) != data[at:at+size]:
                        raise StateError('Game changed modes while reading memory.')
                state = decode(data)
                state['pid'] = self.pid
                return state
            except StateError as exc:
                last_error = exc
                if attempt+1 < attempts:
                    time.sleep(0.05)
        raise last_error

    def close(self):
        if self.handle:
            K.CloseHandle(self.handle)
            self.handle = None


if __name__ == '__main__':
    source = LiveRAM()
    try:
        print(json.dumps(source.snapshot(), indent=2))
    finally:
        source.close()
