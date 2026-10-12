"""Main-menu Item/Magic cursor state for recovery outside battle. Read-only.

Main-menu level and cursors are in reference/ffvii_system_system_menu.cpp
(GP+0x90 menu id, GP+0x220 level, GP+0x228 chosen member, 0x8009A0C8 cursor
structs). The Item and Magic overlays keep their own 0x12-byte cursor structs;
those and the phase bytes were found by RAM differencing and live-verified on
SCUS-94163 on 2026-10-11 (field 250): a Phoenix Down revived Tifa (0 -> 152 HP,
slot emptied) and two Cures raised Tifa 152 -> 538 and Red XIII 182 -> 568 for
5 MP each. Only Cure in the first grid cell is verified; no other spell,
Arrange, Key Items, Summon or Enemy-Skill navigation is attempted.
"""
import struct

from battle_menu import step_towards

GP = 0x62D44
MAIN_CURSORS = 0x9A0C8
ITEM_TOP, ITEM_LIST, ITEM_TARGET, ITEM_PHASE = 0x1D3DDC, 0x1D3DEE, 0x1D3E00, 0x1D3E48
MAGIC_TOP, MAGIC_TARGET, MAGIC_GRID, MAGIC_PHASE = 0x1D2A7C, 0x1D2A8E, 0x1D2AA0, 0x1D2930
ITEM_PHASES = {0: 'top', 1: 'list', 2: 'target'}
MAGIC_PHASES = {0: 'top', 2: 'list', 1: 'target'}
MAIN_ITEM, MAIN_MAGIC = 0, 1
CURE_CELL = 0


def cursor(data, at, shape):
    """Cursor struct: +2 scroll row, +4 columns, +6 rows, +0xA column,
    +0xB row in view, +0xC/+0xD visible columns/rows. The overlay memory is
    reused by other screens, so an unexpected shape means "not this menu"."""
    rows, = struct.unpack_from('<H', data, at+6)
    if (data[at+4], rows, data[at+0xC], data[at+0xD]) != shape:
        return None
    scroll, = struct.unpack_from('<H', data, at+2)
    return dict(col=data[at+0xA], row=scroll+data[at+0xB])


def read_menu(data):
    """Decoded main-menu position, or None while the menu ignores input."""
    u32 = lambda at: struct.unpack_from('<I', data, at)[0]
    if u32(GP+0x27C) != 1:
        return None
    screen = u32(GP+0x90)
    if screen == 0:
        return dict(screen='main', level=u32(GP+0x220), cursor=data[MAIN_CURSORS+0xB],
                    member=data[MAIN_CURSORS+0x12+0xB])
    if screen == 1:
        top, rows, target = (cursor(data, ITEM_TOP, (3, 1, 3, 1)), cursor(data, ITEM_LIST, (1, 320, 1, 10)),
                             cursor(data, ITEM_TARGET, (1, 3, 1, 3)))
        phase = ITEM_PHASES.get(data[ITEM_PHASE])
        if top and rows and target and phase:
            return dict(screen='item', phase=phase, column=top['col'], slot=rows['row'], target=target['row'])
    if screen == 2:
        top, target, grid = (cursor(data, MAGIC_TOP, (1, 3, 1, 3)), cursor(data, MAGIC_TARGET, (1, 3, 1, 3)),
                             cursor(data, MAGIC_GRID, (3, 18, 3, 7)))
        phase = MAGIC_PHASES.get(data[MAGIC_PHASE])
        if top and target and grid and phase:
            return dict(screen='magic', phase=phase, member=u32(GP+0x228), row=top['row'],
                        cell=grid['row']*3+grid['col'], target=target['row'])
    return dict(screen='other')


def next_input(menu, step, slot=None):
    """One pad input advancing `step` (field_heal.Step), or (None, reason).

    `slot` is the inventory position of step.item. Anything that is not on the
    way to the step is backed out of with Cross, never confirmed.
    """
    if menu is None:
        return None, 'Waiting for the menu to accept input.'
    screen = menu['screen']
    if screen == 'main':
        if menu['level'] == 0:
            move = step_towards(menu['cursor'], MAIN_ITEM if step.kind == 'item' else MAIN_MAGIC)
            return (move, 'Move the main-menu cursor.') if move else ('confirm', f'Open the {step.kind} menu.')
        if menu['level'] == 1 and step.kind == 'magic':
            move = step_towards(menu['member'], step.caster)
            return (move, 'Move to the caster.') if move else ('confirm', 'Choose the caster.')
        return 'cancel', 'Back out of the member selection.'
    if screen == 'item' and step.kind == 'item' and slot is not None:
        if menu['phase'] == 'top':
            return ('left', 'Move to Use.') if menu['column'] else ('confirm', 'Choose Use.')
        if menu['phase'] == 'list':
            move = step_towards(menu['slot'], slot)
            return (move, 'Move the item cursor.') if move else ('confirm', 'Select the item.')
        if menu['slot'] != slot:
            return 'cancel', 'Selected item differs from the plan.'
        move = step_towards(menu['target'], step.target)
        return (move, 'Move to the party member.') if move else ('confirm', step.reason)
    if screen == 'magic' and step.kind == 'magic' and menu['member'] == step.caster:
        if menu['phase'] == 'top':
            return ('up', 'Move to Magic.') if menu['row'] else ('confirm', 'Choose Magic.')
        if menu['phase'] == 'list':
            move = step_towards(menu['cell'], CURE_CELL, 3)
            return (move, 'Move to Cure.') if move else ('confirm', 'Select Cure.')
        move = step_towards(menu['target'], step.target)
        return (move, 'Move to the party member.') if move else ('confirm', step.reason)
    return 'cancel', 'Leave this menu screen.'


def commits(menu, action):
    """True when `action` spends the item or MP rather than navigating."""
    return bool(menu and action == 'confirm' and menu.get('phase') == 'target')
