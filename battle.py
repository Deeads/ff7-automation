"""PSX battle records; distinct from the field/save party structures.

Layout source: q-gears-reversing-data/reversing/ffvii/ffvii_battle/ffvii_address.txt.
Battle inputs are opt-in until validated against a live battle on this build.
"""
import struct

OVERLAY_SIGNATURE = bytes.fromhex('00000000 14110000 1d000000 1f000000')
LIMIT_MASK = 0x163762   # players with a full limit bar (ffvii_battle_ffvii_address.txt)
DEAD_MASK = 0x163766
UNIT_ENABLED, UNIT_INVISIBLE = 0x0008, 0x4000
INESCAPABLE = 9         # scene escape counter >= 9: the battle cannot be escaped


def decode_battle(data):
    if data[0xA0000:0xA0010] != OVERLAY_SIGNATURE:
        return None
    u16 = lambda at: struct.unpack_from('<H', data, at)[0]
    u32 = lambda at: struct.unpack_from('<I', data, at)[0]
    player_mask, enemy_mask = u16(0xF83BC), u16(0xF83BE)
    if not player_mask & 7 or player_mask & ~7 or enemy_mask & ~0x3F0 or not enemy_mask:
        return None
    players, enemies = [], []
    for slot in range(10):
        mask = 1 << slot
        if not (player_mask | enemy_mask) & mask:
            continue
        at = 0xF83E0 + slot*0x68
        hp, max_hp = u32(at+0x2C), u32(at+0x30)
        mp, max_mp = u16(at+0x28), u16(at+0x2A)
        if not 1 <= max_hp <= 4_000_000 or hp > max_hp or mp > max_mp:
            return None
        flags = u32(at+4)
        unit = dict(slot=slot, id=data[at+8] if slot<3 else u16(at+0x24),
                    hp=hp, max_hp=max_hp, mp=mp, max_mp=max_mp,
                    status=u32(at), flags=flags, alive=hp>0 and not u32(at)&1)
        # Unit flags +4: 0x0008 enabled, 0x4000 invisible (address notes).
        # Live 2026-09-29: a retracted Hammer Blaster dropped 0x0008 for 30 s
        # while Circle-mashing hit Tifa; helper parts never have it.
        unit['targetable'] = unit['alive'] and bool(flags & UNIT_ENABLED) and not flags & UNIT_INVISIBLE
        (players if player_mask & mask else enemies).append(unit)
    limit_mask, dead_mask = u16(LIMIT_MASK), u16(DEAD_MASK)
    for unit in players:
        unit['limit_ready'] = bool(limit_mask >> unit['slot'] & 1)
    escape = u16(0xF7DB2)
    return dict(id=u16(0x707BC), players=players, enemies=enemies,
                menu_state=struct.unpack_from('<h', data, 0xF3896)[0],
                escape_difficulty=escape, escapable=escape < INESCAPABLE,
                limit_mask=limit_mask, dead_mask=dead_mask, result_flags=u16(0x707BE),
                raw={unit['slot']: data[0xF83E0+unit['slot']*0x68:0xF83E0+unit['slot']*0x68+0x68].hex()
                     for unit in players+enemies},
                validation='player HP/max HP cross-checked live against save records; enemy HP pending')


def validate_players(battle, party):
    """Independent check: battle player max HP must equal the save records'
    max HP (materia-adjusted) for the same party slots."""
    problems = []
    if len(battle['players']) != len(party):
        problems.append(f'{len(battle["players"])} battle players vs {len(party)} party members.')
    for unit, member in zip(battle['players'], party):
        if unit['max_hp'] != member['max_hp']:
            problems.append(f'Slot {unit["slot"]}: battle max HP {unit["max_hp"]} != save {member["max_hp"]}.')
    return problems


def battle_choices(state, enabled=False):
    choices = {'wait': 'Keep all controller buttons released.'}
    battle = state.get('battle')
    if not enabled:
        return choices, 'Battle detected; experimental battle assist is not enabled.'
    if not battle or not battle['players']:
        return choices, 'Waiting for valid battle records.'
    if battle.get('menu'):
        # Verified cursor map: strategy + feedback navigation.
        from battle_strategy import choose
        from battle_menu import next_input
        action = choose(battle, battle['menu']['actor'], state['party'],
                        {int(k): v for k, v in state.get('items', {}).items()},
                        boss=state.get('flee_exhausted', False))
        pad_input, why = next_input(battle['menu'], action)
        if pad_input:
            choices[pad_input] = f'{why} Plan: {action.reason}'
        return choices, f'{action.command}: {action.reason} ({why})'
    living = [p for p in battle['players'] if p['alive']]
    if not living:
        return choices, 'All party members are incapacitated.'
    critical = min(p['hp']/p['max_hp'] for p in living) < 0.30 or len(living)<len(battle['players'])
    if critical and battle['escape_difficulty'] < INESCAPABLE and not state.get('flee_exhausted'):
        choices['flee'] = 'Hold L1 and R1 to try escaping because party health is critical.'
        return choices, 'Attempt escape at critical health.'
    # Inescapable, or escape already failed: keep fighting rather than idling.
    if not any(e.get('targetable', e['alive']) for e in battle['enemies']):
        # Every enemy is hidden/retracted: the default target would be an ally.
        if state.get('battle_target_open'):
            choices['cancel'] = 'Back out of target selection: no enemy can be targeted right now.'
            return choices, 'No targetable enemy; cancel the pending attack so it cannot hit an ally.'
        return choices, 'No targetable enemy (hidden or retracted); hold the attack.'
    if 0 <= battle['menu_state'] < 28:
        choices['confirm'] = 'Confirm the highlighted battle command or target (basic attack assist).'
    return choices, 'Basic battle confirmation; no automatic item or spell selection.'
