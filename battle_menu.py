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
    if not data.get('verified') or any(f not in data for f in FIELDS):
        return None
    return data


def read_menu(data, menu_map):
    """Decode the menu fields named in the verified map from raw RAM."""
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
    if action.command == 'flee':
        return 'flee', 'Hold L1+R1 to escape.'
    if phase == phases['command']:
        wanted = COMMAND_IDS.get(action.command)
        if wanted not in menu['command_ids']:
            return None, f'Command {action.command} is not in this character\'s menu.'
        move = step_towards(menu['command_cursor'], menu['command_ids'].index(wanted), columns)
        return (move, 'Move the command cursor.') if move else ('confirm', f'Choose {action.command}.')
    if phase == phases['submenu']:
        wanted = action.spell if action.command == 'magic' else action.item
        if wanted not in menu['submenu_ids']:
            return 'cancel', 'Wanted spell/item is not available; back out.'
        move = step_towards(menu['submenu_cursor'], menu['submenu_ids'].index(wanted), 2 if action.command == 'magic' else 1)
        return (move, 'Move the submenu cursor.') if move else ('confirm', 'Select the spell or item.')
    if phase == phases['target']:
        if menu['target_cursor'] == action.target_slot:
            return 'confirm', f'Confirm target slot {action.target_slot}.'
        return 'right', 'Cycle the target cursor.'
    return None, 'No command menu open.'
