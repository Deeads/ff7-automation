"""Read active spells and enemy defenses from the loaded USA battle RAM.

Layouts: reference/ffvii_system_ffvii_address.txt (0x440 player records)
and reference/ffvii_battle_ffvii_address.txt (kernel attacks and enemy data).
No game data is downloaded, bundled, or written back to the emulator.
"""
import struct

SPELL_NAMES = {0: 'Cure', 1: 'Cure2', 2: 'Cure3', 4: 'Poisona', 5: 'Esuna',
               7: 'Life', 8: 'Life2', 27: 'Fire', 28: 'Fire2', 29: 'Fire3',
               30: 'Ice', 31: 'Ice2', 32: 'Ice3', 33: 'Bolt', 34: 'Bolt2', 35: 'Bolt3'}
RESPONSES = {0: 'death', 2: 'weak', 3: 'normal', 4: 'resist',
             5: 'immune', 6: 'absorb', 7: 'recover'}


def enrich(data, battle):
    u16 = lambda at: struct.unpack_from('<H', data, at)[0]
    u32 = lambda at: struct.unpack_from('<I', data, at)[0]
    for actor in battle['players']:
        base = 0x9D84C + actor['slot'] * 0x440
        commands = [data[base+0x4C+i*6] for i in range(16)]
        if any(c != 255 and not 1 <= c <= 28 for c in commands):
            continue
        # A stale player cache must not grant spells to a different party.
        if u16(base+0x12) != actor['max_hp'] or u16(base+0x16) != actor['max_mp']:
            continue
        actor['commands'] = [c for c in commands if c != 255]
        actor['abilities'] = []
        actor['reflect'] = bool(u16(0xF836C) & (1 << actor['slot']))
        # Derive Silence from the owned Echo Screen's removal mask.
        silence_mask = u32(0x722CC + 12*0x1C + 0x14)
        actor['silenced'] = silence_mask != 0xFFFFFFFF and bool(actor['status'] & silence_mask)
        for i in range(56):
            at = base+0x108+i*8
            spell, cost = data[at], data[at+1]
            if spell == 255 or spell not in SPELL_NAMES or any(a['id'] == spell for a in actor['abilities']):
                continue
            attack = 0x708C4 + spell*0x1C
            kind = ('heal' if spell in (0, 1, 2) else 'revive' if spell in (7, 8)
                    else 'cure_status' if spell in (4, 5) else 'damage')
            actor['abilities'].append(dict(id=spell, name=SPELL_NAMES[spell], kind=kind,
                mp=cost, power=data[attack+0xF], elements=u16(attack+0x18),
                removes=u32(attack+0x14) if kind == 'cure_status' else 0,
                # Ranking estimate only; never used as guaranteed damage.
                potency=data[attack+0xF], available=True, menu_index=i))
    for enemy in battle['enemies']:
        unit = 0xF83E0 + enemy['slot']*0x68
        pointer = u32(0xF5BB8 + enemy['slot']*0x44 + 0x38)
        offset = pointer - 0x800F5F44
        if offset < 0 or offset >= 3*0xB8 or offset % 0xB8:
            continue
        record = pointer - 0x80000000
        pairs = [(data[record+0x28+i], data[record+0x30+i]) for i in range(8)]
        if any(e != 255 and (e > 15 or r not in RESPONSES) for e, r in pairs):
            continue
        affinity = {str(e): RESPONSES[r] for e, r in pairs if e != 255}
        for bit in range(16):
            if u16(unit+0x26) & (1 << bit): affinity[str(bit)] = 'absorb'
            elif u16(unit+0x54) & (1 << bit): affinity[str(bit)] = 'immune'
        enemy.update(affinities=affinity, affinities_known=True,
                     reflect=bool(u16(0xF836C) & (1 << enemy['slot'])))
    battle['tactics_data'] = any('abilities' in p for p in battle['players'])
    return battle
