"""Read-only scan of the live field event section for treasure and save points.

The event section layout and opcode operand lengths come from
reference/field.py (ff7tools, C. Bauer); bank offsets from reference/savemap.md
and FieldStoryTargetReader.cs. Nothing here writes memory.

A *treasure* is an entity whose Talk/other scripts grant an item, materia or
gil (STITM/SMTRA/GOLD+) and set a literal save-bank bit (BITON) that the game
itself uses as the "already taken" flag. Scripts that ask a dialogue question
are excluded: collecting must never answer an unverified choice.
"""
import struct

# Operand byte counts per opcode; 0xFF marks an unused opcode.
SIZES = bytes.fromhex(
    '0002020202020201010e0505ffff010001020102050607080708ffffffffffff0a0104020208010100000101040601090303030101'
    '030407050505030000000002040501ff04ff0406030101ff04ff04090503010102060604040406070907000901040505000800080106'
    '080003020503010203030703040304020202020102030403030303040304030403040304030403040304020202020203040506060a01'
    '010202010a080805050103000502020404030205050103040302040403ff010a070e0b0002020101010302020201010c01010f090903'
    '0302000e010300000a0303020202040404060909040407070a01040d01010101030100020101050200')
SPCAL_SIZES = {0xf5: 1, 0xf6: 4, 0xf7: 2, 0xf8: 2, 0xf9: 0, 0xfa: 0, 0xfb: 1, 0xfc: 1, 0xfd: 2, 0xfe: 0, 0xff: 0}
RET, SPCAL, IF, KAWAI, ASK, MENU = 0x00, 0x0f, 0x14, 0x28, 0x48, 0x49
GOLD_ADD, STITM, SMTRA, BITON = 0x39, 0x58, 0x5b, 0x82
SAVE_MENU = 14
# Byte-addressed save banks -> offset of that bank inside the savemap.
SAVE = 0x9C6E4
BYTE_BANKS = {1: 0xBA4, 3: 0xCA4, 11: 0xDA4, 13: 0xEA4, 15: 0xFA4}

assert len(SIZES) == 256


class ScriptError(ValueError):
    pass


def instruction_size(code, at):
    op = code[at]
    if op == SPCAL:
        sub = code[at+1]
        if sub not in SPCAL_SIZES:
            raise ScriptError(f'Unknown SPCAL sub-opcode {sub:#x} at {at:#x}.')
        return SPCAL_SIZES[sub]+2
    if op == KAWAI:
        return max(code[at+1], 1)
    if SIZES[op] == 0xFF:
        raise ScriptError(f'Unknown opcode {op:#x} at {at:#x}.')
    return SIZES[op]+1


def parse_section(section):
    if len(section) < 32:
        raise ScriptError('Event section too small.')
    _, actors, _, strings, extras = struct.unpack_from('<HBBHH', section)
    at = 32
    names = []
    for _ in range(actors):
        names.append(section[at:at+8].split(b'\0')[0].decode('ascii', errors='replace'))
        at += 8
    at += 4*extras
    tables = []
    for _ in range(actors):
        tables.append(list(struct.unpack_from('<32H', section, at)))
        at += 64
    if not at <= strings <= len(section):
        raise ScriptError('Invalid event section string table offset.')
    return dict(names=names, tables=tables, code_start=at, code_end=strings)


def instructions(section, start, end):
    at = start
    while at < end:
        size = instruction_size(section, at)
        yield at, section[at], bytes(section[at+1:at+size])
        at += size


def script_ranges(parsed):
    """(entity, script index, start, end) for every distinct entry point."""
    entries = sorted({e for table in parsed['tables'] for e in table
                      if parsed['code_start'] <= e < parsed['code_end']} | {parsed['code_end']})
    following = {entries[i]: entries[i+1] for i in range(len(entries)-1)}
    seen = set()
    for entity, table in enumerate(parsed['tables']):
        for index, start in enumerate(table):
            if start in following and (entity, start) not in seen:
                seen.add((entity, start))
                yield entity, index, start, following[start]


def literal_bit(operands):
    """BITON/IF bank byte -> (bank, address, bit) when both are literal byte banks."""
    banks, address, bit = operands[0], operands[1], operands[2]
    bank, source = banks >> 4, banks & 15
    if source or bank not in BYTE_BANKS or bit > 7:
        return None
    return bank, address, bit


def scan(section):
    parsed = parse_section(section)
    found = {}
    for entity, index, start, end in script_ranges(parsed):
        if index == 0:
            continue  # init/main: story scripting, not something the player opens
        info = found.setdefault(entity, dict(grants=[], sets=[], tests=set(), asks=False, save=False))
        for at, op, operands in instructions(section, start, end):
            if op == STITM:
                info['grants'].append(dict(kind='item', id=struct.unpack_from('<H', operands, 1)[0],
                                           amount=operands[3], literal=operands[0] == 0))
            elif op == SMTRA:
                info['grants'].append(dict(kind='materia', id=operands[2], literal=operands[0] == 0 and operands[1] == 0))
            elif op == GOLD_ADD:
                info['grants'].append(dict(kind='gil', amount=struct.unpack_from('<I', operands, 1)[0], literal=operands[0] == 0))
            elif op == BITON:
                flag = literal_bit(operands)
                if flag:
                    info['sets'].append(flag)
            elif op == IF and operands[3] in (9, 10) and not operands[0] & 15:
                # IF bank(A)/literal B, bit test: operands = banks, A, B, op, jump.
                flag = literal_bit(bytes((operands[0], operands[1], operands[2])))
                if flag:
                    info['tests'].add(flag)
            elif op == ASK:
                info['asks'] = True
            elif op == MENU and operands[1] == SAVE_MENU:
                info['save'] = True
    treasures, saves, skipped = [], [], []
    for entity, info in sorted(found.items()):
        name = parsed['names'][entity]
        if info['save']:
            saves.append(dict(entity=entity, name=name))
        if not info['grants']:
            continue
        if info['asks']:
            skipped.append(dict(entity=entity, name=name, reason='Script asks a dialogue question.'))
            continue
        guarded = [flag for flag in info['sets'] if flag in info['tests']]
        flag = (guarded or info['sets'] or [None])[0]
        if flag is None:
            skipped.append(dict(entity=entity, name=name, reason='No literal save-bank flag is set.'))
            continue
        treasures.append(dict(entity=entity, name=name, flag=list(flag), guarded=bool(guarded),
                              grants=info['grants']))
    return dict(treasures=treasures, save_points=saves, skipped=skipped)


def flag_set(data, flag):
    bank, address, bit = flag
    return bool(data[SAVE+BYTE_BANKS[bank]+address] >> bit & 1)


_cache = {}


def field_checks(data, section_offset):
    """Treasure/save-point summary for the loaded field, with live flag values."""
    strings = struct.unpack_from('<H', data, section_offset+4)[0]
    section = bytes(data[section_offset:section_offset+max(strings, 32)])
    result = _cache.get(section)
    if result is None:
        try:
            result = scan(section)
        except (ScriptError, struct.error, IndexError) as exc:
            result = dict(error=f'{type(exc).__name__}: {exc}', treasures=[], save_points=[], skipped=[])
        if len(_cache) > 8:
            _cache.clear()
        _cache[section] = result
    treasures = [dict(t, taken=flag_set(data, t['flag'])) for t in result['treasures']]
    return dict(result, treasures=treasures)


# ---- Story-trigger scan (used by explore.py when the route has no step) ----
LINE, MAPJUMP = 0xD0, 0x60
# Model entities: 2 = talk, 3 = contact. LINE entities: 2 = [OK], 3-7 = move/go.
TALK, CONTACT = 2, 3


def _script_body(section, parsed, entity, index):
    table = parsed['tables'][entity]
    start = table[index]
    if not parsed['code_start'] <= start < parsed['code_end']:
        return []
    later = sorted({e for t in parsed['tables'] for e in t if start < e <= parsed['code_end']} | {parsed['code_end']})
    ops = []
    for at, op, operands in instructions(section, start, later[0]):
        ops.append((op, operands))
        if op == RET:
            break
    return ops


def story_triggers(section):
    """LINE triggers and talkable entities whose scripts actually do something.

    Returns dict(lines=[...], talks=[...]). A script that is only RET is ignored.
    Talk scripts that ask a question or open a menu (shops, inns, save) are
    flagged so exploration never answers an unverified choice.
    """
    parsed = parse_section(section)
    lines, talks = [], []
    for entity, name in enumerate(parsed['names']):
        init = _script_body(section, parsed, entity, 0)
        line = next((operands for op, operands in init if op == LINE), None)
        bodies = {i: _script_body(section, parsed, entity, i) for i in range(2, 8)}
        live = {i: ops for i, ops in bodies.items() if any(op != RET for op, _ in ops)}
        if line is not None and len(line) >= 12:
            a = list(struct.unpack_from('<3h', line, 0))
            b = list(struct.unpack_from('<3h', line, 6))
            kinds = sorted(live)
            if kinds:
                lines.append(dict(entity=entity, name=name, line=[a, b],
                                  ok=2 in live and not any(i >= 3 for i in live), scripts=kinds,
                                  jumps=any(op == MAPJUMP for ops in live.values() for op, _ in ops),
                                  asks=any(op in (ASK, MENU) for ops in live.values() for op, _ in ops)))
        elif TALK in live:
            ops = live[TALK]
            talks.append(dict(entity=entity, name=name,
                              asks=any(op in (ASK, MENU) for op, _ in ops),
                              jumps=any(op == MAPJUMP for op, _ in ops)))
    return dict(lines=lines, talks=talks)


_trigger_cache = {}


def field_triggers(data, section_offset):
    strings = struct.unpack_from('<H', data, section_offset+4)[0]
    section = bytes(data[section_offset:section_offset+max(strings, 32)])
    result = _trigger_cache.get(section)
    if result is None:
        try:
            result = story_triggers(section)
        except (ScriptError, struct.error, IndexError) as exc:
            result = dict(error=f'{type(exc).__name__}: {exc}', lines=[], talks=[])
        if len(_trigger_cache) > 8:
            _trigger_cache.clear()
        _trigger_cache[section] = result
    return result
