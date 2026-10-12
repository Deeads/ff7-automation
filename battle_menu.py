"""Battle command-menu navigation by cursor feedback.

Menu cursor addresses are not in the reference notes; they are discovered by
`battle_probe.py` in a live battle and recorded in battle_menu.json. Until
that file says "verified": true, `next_input` refuses to navigate and the loop
keeps the old basic assist. Each call looks at fresh state and returns one
input, so a missed press is simply corrected on the next tick.
"""
import json
from pathlib import Path

MAP = Path(__file__).resolve().parent/'battle_menu.json'
FIELDS = ('actor', 'command_cursor', 'command_ids', 'submenu_cursor', 'submenu_ids', 'target_cursor', 'phase')
# Command ids from KERNEL.BIN section 17: 1 Attack, 2 Magic, 4 Item, 20 Limit.
COMMAND_IDS = dict(attack=1, magic=2, item=4, limit=20)


_cache = {}


def load_map(path=MAP):
    path = Path(path)
    try:
        stamp = path.stat().st_mtime
    except FileNotFoundError:
        return None
    if _cache.get('key') != (path, stamp):
        _cache.update(key=(path, stamp), data=json.loads(path.read_text(encoding='utf-8')))
    data = _cache['data']
    if data.get('verified') and data.get('adapter') == 'ff7-us-native-v1':
        from emulators import KERNEL_HASH
        if data.get('kernel_hash') != KERNEL_HASH:
            return None
        return data
    if not data.get('verified') or any(f not in data for f in FIELDS):
        return None
    return data


def read_menu(data, menu_map):
    """Decode the menu fields named in the verified map from raw RAM."""
    if menu_map.get('adapter') == 'ff7-us-native-v1':
        actor = data[0xF38A0]
        if actor > 2:
            return None
        at = 0x9D84C+actor*0x440
        ui = actor*0x240
        phase = int.from_bytes(data[0xF3896:0xF3898], 'little', signed=True)
        commands = [data[at+0x4C+i*6] for i in range(16)]
        spells = [data[at+0x108+i*8] for i in range(56)]
        row, col = data[0xF90BF+ui], data[0xF90BE+ui]
        spell_row, spell_col = data[0xF90E3+ui], data[0xF90E2+ui]
        # Item list: cursor struct 1 (+2 scroll, +6 rows, +0xB row in view) over the
        # battle item array at 0x801671B8 (6 bytes each: id, quantity, target, restriction).
        # Live 2026-10-11: phase 5, row 1 -> selected id 3 (Ether), scroll 9 + row 2 -> id 1.
        # The array is filled when the battle starts; the cursor's own row count
        # stays 0 until the Item menu is opened once, so it must not bound the
        # read (live 2026-10-12: 4 Phoenix Downs unseen, two members left KO).
        item_at = 0xF90B4+ui+0x12
        item_rows = 320
        return dict(actor=actor, phase=phase, phases=dict(command=1, submenu=6, target=0, item=5, limit=24),
                    item_cursor=int.from_bytes(data[item_at+2:item_at+4], 'little')+data[item_at+0xB],
                    item_ids=[int.from_bytes(data[0x1671B8+i*6:0x1671BA+i*6], 'little') for i in range(item_rows)],
                    item_stock=[data[0x1671BA+i*6] for i in range(item_rows)],
                    command_cursor=col*4+row, command_ids=commands,
                    submenu_cursor=spell_row*3+spell_col, submenu_ids=spells,
                    target_cursor=data[0xF38A9], selected_command=data[0xF389C],
                    selected_ability=int.from_bytes(data[0xF389E:0xF38A0], 'little'),
                    capabilities=menu_map.get('capabilities', []), native=True,
                    # Scroll/multiple-command columns need separate live checks.
                    navigable=row<4 and col==0 and spell_row<3 and spell_col<3)
    def value(spec):
        at, size = spec['address'], spec.get('size', 1)
        return int.from_bytes(data[at:at+size], 'little', signed=spec.get('signed', False))

    def array(spec):
        return [value(dict(spec, address=spec['address']+i*spec.get('size', 1)))
                for i in range(spec['count'])]
    return dict(actor=value(menu_map['actor']), phase=value(menu_map['phase']),
                command_cursor=value(menu_map['command_cursor']), command_ids=array(menu_map['command_ids']),
                submenu_cursor=value(menu_map['submenu_cursor']), submenu_ids=array(menu_map['submenu_ids']),
                target_cursor=value(menu_map['target_cursor']), phases=menu_map['phases'])


def step_towards(current, wanted, columns=1):
    """Grid cursor: rows of `columns`; move vertically first, then across."""
    if current == wanted:
        return None
    row, col = divmod(current, columns)
    want_row, want_col = divmod(wanted, columns)
    if row != want_row:
        return 'down' if want_row > row else 'up'
    return 'right' if want_col > col else 'left'


def next_input(menu, action, columns=1):
    """One pad input advancing `action` given the decoded menu, or (None, reason)."""
    if menu is None:
        return None, 'Battle menu addresses are not live-verified; basic assist only.'
    phases = menu['phases']
    phase = menu['phase']
    if action.command == 'wait':
        return None, action.reason
    if action.command == 'switch':
        return ('switch', action.reason) if phase == phases['command'] else ('cancel', 'Return to commands before switching characters.')
    if menu.get('native') and not menu.get('navigable'):
        return None, 'Menu scrolled beyond the verified cursor layout.'
    if action.command == 'flee':
        return 'flee', 'Hold L1+R1 to escape.'
    if phase == phases['command']:
        wanted = COMMAND_IDS.get(action.command)
        if wanted not in menu['command_ids']:
            return None, f'Command {action.command} is not in this character\'s menu.'
        move = step_towards(menu['command_cursor'], menu['command_ids'].index(wanted), columns)
        return (move, 'Move the command cursor.') if move else ('confirm', f'Choose {action.command}.')
    if phase == phases['submenu']:
        if menu.get('native') and action.command != 'magic':
            return 'cancel', 'Submenu does not match the planned command.'
        wanted = action.spell if action.command == 'magic' else action.item
        if wanted not in menu['submenu_ids']:
            return 'cancel', 'Wanted spell/item is not available; back out.'
        wanted_index = menu['submenu_ids'].index(wanted)
        if menu.get('native') and wanted_index >= 9:
            return 'cancel', 'Spell requires an unverified scrolling layout.'
        move = step_towards(menu['submenu_cursor'], wanted_index, 3 if menu.get('native') else 2 if action.command == 'magic' else 1)
        return (move, 'Move the submenu cursor.') if move else ('confirm', 'Select the spell or item.')
    if menu.get('native') and phase == phases.get('item'):
        if action.command != 'item' or action.item not in menu['item_ids']:
            return 'cancel', 'Item list does not match the plan; back out.'
        move = step_towards(menu['item_cursor'], menu['item_ids'].index(action.item))
        return (move, 'Move the item cursor.') if move else ('confirm', 'Select the item.')
    if menu.get('native') and phase == phases.get('limit'):
        # Only the first Limit of the list is used; its cursor has not been moved live.
        return ('confirm', 'Select the Limit.') if action.command == 'limit' else ('cancel', 'Limit list does not match the plan.')
    if phase == phases['target']:
        if menu.get('native'):
            if menu['selected_command'] != COMMAND_IDS.get(action.command):
                return 'cancel', 'Selected command changed; cancel before targeting.'
            if action.command == 'magic' and menu['selected_ability'] != action.spell:
                return 'cancel', 'Selected spell differs from the plan.'
            if action.command == 'item' and menu['selected_ability'] != action.item:
                return 'cancel', 'Selected item differs from the plan.'
        if menu['target_cursor'] == action.target_slot:
            return 'confirm', f'Confirm target slot {action.target_slot}.'
        return 'right', 'Cycle the target cursor.'
    return None, 'No command menu open.'
