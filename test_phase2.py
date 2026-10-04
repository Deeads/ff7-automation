"""Phase 2: field-script checks, chest collection, interaction helper, Midgar puzzle.

Synthetic data plus read-only RAM/disc fixtures; nothing touches the game.
"""
import copy
from pathlib import Path
import struct
import time
import unittest
from unittest.mock import patch

from collect import Collector, with_collection
import field_script
from field_script import scan, parse_section, script_ranges, instructions, field_checks
from ff7_state import decode
import motion
from motion import interact
from story import midgar_model_objective, objective, target_action, chest_unlocked, MODEL_PARTS
from test_motion_executor import controller, A, B, RELEASE_EDGE

ROOT = Path(__file__).parent
DISC = Path(r'C:\Users\david\Downloads\gam\Final Fantasy VII (USA) (Disc 1).cue')


def section(entities):
    """entities: [(name, {script_index: code bytes})] -> event section bytes."""
    count = len(entities)
    code_start = 32+8*count+64*count
    code, tables = bytearray(), []
    empty = code_start  # shared single RET for unused slots
    code += b'\x00'
    for name, scripts in entities:
        table = [empty]*32
        for index, body in scripts.items():
            table[index] = code_start+len(code)
            code += body
        tables.append(table)
    strings = code_start+len(code)
    header = struct.pack('<HBBHHH6x8s8s', 0x0502, count, count, strings, 0, 512, b'test', b'test')
    names = b''.join(name.encode().ljust(8, b'\0') for name, _ in entities)
    return header+names+b''.join(struct.pack('<32H', *t) for t in tables)+bytes(code)+b'\0'*4


IF_BIT_ON = lambda bank, addr, bit, jump: bytes((0x14, bank << 4, addr, bit, 9, jump))
STITM = lambda item, qty: bytes((0x58, 0)) + struct.pack('<H', item) + bytes((qty,))
BITON = lambda bank, addr, bit: bytes((0x82, bank << 4, addr, bit))
ASK = bytes((0x48, 0, 1, 2, 0, 1, 7))
SAVE_MENU = bytes((0x49, 0, 14, 0))
RET = b'\x00'


class FieldScriptTests(unittest.TestCase):
    def test_chest_pickup_and_save_point_detected(self):
        chest = IF_BIT_ON(3, 177, 5, 12) + STITM(23, 2) + BITON(3, 177, 5) + RET
        gift = STITM(4, 1) + ASK + BITON(1, 60, 1) + RET          # asks a question: skipped
        unflagged = STITM(1, 1) + RET                             # no flag: skipped
        result = scan(section([('init', {0: RET}), ('TAKARA1', {0: RET, 1: chest}),
                               ('Kid', {1: gift}), ('item', {1: unflagged}), ('SAVE', {1: SAVE_MENU+RET})]))
        self.assertEqual([(t['name'], t['flag'], t['guarded']) for t in result['treasures']],
                         [('TAKARA1', [3, 177, 5], True)])
        self.assertEqual(result['treasures'][0]['grants'], [dict(kind='item', id=23, amount=2, literal=True)])
        self.assertEqual([s['name'] for s in result['save_points']], ['SAVE'])
        self.assertEqual({s['name'] for s in result['skipped']}, {'Kid', 'item'})

    def test_init_script_grants_are_not_treasure(self):
        self.assertEqual(scan(section([('ev', {0: STITM(1, 1)+BITON(1, 2, 3)+RET})]))['treasures'], [])

    def test_variable_bank_flag_is_not_trusted(self):
        body = STITM(1, 1) + bytes((0x82, 0x31, 5, 0)) + RET     # bit number from a variable
        self.assertEqual(scan(section([('box', {1: body})]))['skipped'][0]['reason'], 'No literal save-bank flag is set.')

    def test_live_taken_flag_read_from_savemap(self):
        sec = section([('box', {1: IF_BIT_ON(3, 10, 2, 9)+STITM(5, 1)+BITON(3, 10, 2)+RET})])
        data = bytearray(0x200000)
        data[0x1000:0x1000+len(sec)] = sec
        self.assertFalse(field_checks(bytes(data), 0x1000)['treasures'][0]['taken'])
        data[field_script.SAVE+0xCA4+10] = 1 << 2
        self.assertTrue(field_checks(bytes(data), 0x1000)['treasures'][0]['taken'])

    def test_unknown_opcode_degrades_to_error_not_crash(self):
        sec = section([('box', {1: bytes((0x0c,))+RET})])
        data = bytearray(0x200000)
        data[0x2000:0x2000+len(sec)] = sec
        result = field_checks(bytes(data), 0x2000)
        self.assertIn('ScriptError', result['error'])
        self.assertEqual(result['treasures'], [])

    def test_matches_recorded_model_room_disassembly(self):
        path = ROOT/'fixtures/ram-249-first-part.bin'
        reference = ROOT/'reference/model-room-disassembly.txt'
        if not path.exists() or not reference.exists():
            self.skipTest('Model room fixture unavailable.')
        data = path.read_bytes()
        at = struct.unpack_from('<I', data, 0x9C6DC)[0]-0x80000000
        sec = data[at:at+struct.unpack_from('<H', data, at+4)[0]]
        parsed = parse_section(sec)
        boundaries = {start for _, _, start, _ in script_ranges(parsed)}
        mine = {a for _, _, s, e in script_ranges(parsed) for a, _, _ in instructions(sec, s, e)}
        expected = {int(line.split()[0], 16) for line in reference.read_text().splitlines()
                    if line and not ' script ' in line}
        self.assertTrue(expected <= mine | boundaries)

    @unittest.skipUnless(DISC.exists(), 'Disc 1 image unavailable.')
    def test_disc_floor_63_chests_and_floor_64_lockers(self):
        from field_disc import Disc
        disc = Disc(DISC)
        try:
            files = disc.listing('FIELD')
            floor63 = scan(disc.event_section(*files['BLIN63_1.DAT'][:2]))
            floor64 = scan(disc.event_section(*files['BLIN64.DAT'][:2]))
            floor65 = scan(disc.event_section(*files['BLIN65_1.DAT'][:2]))
        finally:
            disc.close()
        self.assertEqual([(t['name'], t['flag']) for t in floor63['treasures']],
                         [('TAKARA1', [3, 177, 5]), ('TAKARA2', [3, 177, 6]), ('TAKARA3', [3, 177, 7])])
        self.assertEqual([t['name'] for t in floor64['treasures']], ['RLINA', 'RLINB', 'RLINC'])
        # Midgar part chests grant key items via BITON only: story, not collection.
        self.assertEqual(floor65['treasures'], [])


def grid_state(treasures, actors, player=(5, 5, 0)):
    mesh = []
    for x in range(10):
        for y in range(10):
            a, b, c, d = [100*x, 100*y, 0], [100*x+100, 100*y, 0], [100*x, 100*y+100, 0], [100*x+100, 100*y+100, 0]
            mesh += [dict(id=len(mesh), vertices=[a, b, c], neighbors=[-1]*3, locked=False),
                     dict(id=len(mesh)+1, vertices=[b, d, c], neighbors=[-1]*3, locked=False)]
    edges = {}
    for t in mesh:
        for i in range(3):
            edge = tuple(sorted((tuple(t['vertices'][i]), tuple(t['vertices'][(i+1) % 3]))))
            if edge in edges:
                other, j = edges[edge]
                t['neighbors'][i] = other; mesh[other]['neighbors'][j] = t['id']
            else:
                edges[edge] = t['id'], i
    from navigation import locate
    for actor in actors:
        actor.setdefault('triangle', locate(mesh, actor['position']))
    position = [player[0]*10, player[1]*10, 0]
    return dict(mode='field', field_id=245, movement_locked=False, fading=False, dialogs=[],
                player=dict(position=position, triangle=locate(mesh, position), collision_radius=10, facing=0),
                actors=actors, field_checks=dict(treasures=treasures, save_points=[], skipped=[]),
                navigation=dict(mesh=mesh, direction_vectors=dict(up=[0, 1], down=[0, -1], left=[-1, 0], right=[1, 0]),
                                direction_angles=dict(up=0, down=128, left=64, right=192)))


def chest(entity, name, x, y):
    return (dict(entity=entity, name=name, flag=[3, 177, entity], grants=[], taken=False),
            dict(entity=entity, entity_name=name, position=[x, y, 0], talkable=True, visible=True,
                 solid=True, collision_radius=20))


class CollectorTests(unittest.TestCase):
    def setUp(self):
        pairs = [chest(1, 'FAR', 900, 900), chest(2, 'NEAR', 150, 60), chest(3, 'MID', 500, 450)]
        self.state = grid_state([t for t, _ in pairs], [a for _, a in pairs])

    def test_visits_nearest_unopened_first_then_next(self):
        c = Collector()
        order = []
        state = self.state
        for _ in range(3):
            target, reason = c.target(state)
            order.append(target['actor'])
            self.assertIn('before leaving the field', reason)
            state = copy.deepcopy(state)
            next(t for t in state['field_checks']['treasures'] if t['entity'] == target['entity'])['taken'] = True
        self.assertEqual(order, ['NEAR', 'MID', 'FAR'])
        self.assertEqual(c.target(state), (None, None))

    def test_skips_after_two_attempts_without_flag_flip(self):
        c = Collector()
        target, _ = c.target(self.state)
        c.attempted(self.state, target); c.attempted(self.state, target)
        locked = copy.deepcopy(self.state); locked['movement_locked'] = True
        self.assertNotEqual(c.target(locked)[0]['actor'], 'NEAR')   # not judged mid-script ...
        self.assertEqual(c.skipped, {})
        self.assertEqual(c.target(self.state)[0]['actor'], 'MID')   # ... but skipped once settled
        events = c.drain()
        self.assertEqual([e['event'] for e in events], ['collect_attempt', 'collect_attempt', 'collect_skipped'])
        self.assertIn('did not flip after 2 tries', events[-1]['reason'])

    def test_unreachable_or_invisible_treasure_ignored(self):
        state = copy.deepcopy(self.state)
        state['actors'][1]['visible'] = False
        for t in state['navigation']['mesh']:
            if t['id'] == state['actors'][2]['triangle']:
                t['locked'] = True
        self.assertEqual(Collector().target(state)[0]['actor'], 'FAR')

    def test_detour_only_before_leaving(self):
        c = Collector()
        gateway = dict(field_id=245, position=[990, 500, 0], radius=0, gateway=1)
        self.assertEqual(with_collection(c, self.state, gateway, 'Continue to floor 64.')[0]['actor'], 'NEAR')
        npc = dict(field_id=245, position=[300, 300, 0], actor='ZAKOA')
        self.assertIs(with_collection(c, self.state, npc, 'Talk.')[0], npc)
        self.assertIs(with_collection(None, self.state, gateway, 'Go.')[0], gateway)


class InteractTests(unittest.TestCase):
    class Source:
        def __init__(self, states):
            self.states = states
        def snapshot(self, attempts):
            return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def near_chest(self, facing):
        _, actor = chest(2, 'NEAR', 150, 60)
        state = grid_state([], [actor], player=(10, 6))
        state['player']['facing'] = facing
        return state, dict(field_id=245, position=actor['position'], triangle=actor['triangle'], radius=50, actor='NEAR')

    def test_faces_then_confirms_after_cross_release_edge(self):
        wrong, target = self.near_chest(facing=0)
        right = copy.deepcopy(wrong); right['player']['facing'] = 192
        c = controller()
        c.set_movement('right', True)                     # arriving at run
        result = interact(c, self.Source([right]), wrong, target, lambda: False)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['turns'], 1)
        masks = [m for _, m in c.pad.reports]
        confirm = masks.index(B)
        self.assertFalse(any(m & A for m in masks[1:confirm]))
        released = next(t for (t, m) in c.pad.reports[1:] if not m & A)
        self.assertGreaterEqual(c.pad.reports[confirm][0]-released, RELEASE_EDGE-0.002)
        self.assertEqual(masks[-1], 0)

    def test_out_of_range_never_presses_circle(self):
        state, target = self.near_chest(facing=192)
        state['player']['position'] = [800, 800, 0]
        from navigation import locate
        state['player']['triangle'] = locate(state['navigation']['mesh'], [800, 800, 0])
        c = controller()
        result = interact(c, self.Source([state]), state, target, lambda: False)
        self.assertFalse(result['confirmed'])
        self.assertFalse(any(m & B for _, m in c.pad.reports))


def puzzle(field=248, placed=0, carried=0, chests=(0, 0), keycard66=False):
    keys = [0]*8
    keys[4] = 1 | carried | (2 if keycard66 else 0)
    return dict(mode='field', field_id=field, key_items=keys, story_progress=264,
                puzzles=dict(midgar_placed=placed, shinra_chests=list(chests)))


class MidgarPuzzleTests(unittest.TestCase):
    def objective_kind(self, state):
        with patch('story.actor_target', side_effect=lambda s, name: dict(actor=name)), \
             patch('story.gateway_target', side_effect=lambda s, dest: dict(gateway=dest)):
            target, reason = midgar_model_objective(state)
        if target is None:
            return reason
        return target.get('actor') or target.get('gateway') or target.get('triangle')

    def test_full_state_machine(self):
        chest_bit = {name: (byte, mask) for name, _, _, _, _, byte, mask in MODEL_PARTS}
        placed, chests, steps = 0, [0, 0], []
        for name, carried, bit, point, triangle, byte, mask in MODEL_PARTS:
            steps.append(self.objective_kind(puzzle(248, placed, 0, chests)))          # open chest
            chests[byte] |= mask
            steps.append(self.objective_kind(puzzle(248, placed, carried, chests)))    # carry to 249
            steps.append(self.objective_kind(puzzle(249, placed, carried, chests)))    # slot triangle
            placed |= bit
            steps.append(self.objective_kind(puzzle(249, placed, 0, chests)))          # back out
        steps.append(self.objective_kind(puzzle(248, placed, 0, chests)))              # Keycard 66 chest
        steps.append(self.objective_kind(puzzle(248, placed, 0, chests, keycard66=True)))
        self.assertEqual(steps, ['PARTD', 249, 70, 248, 'PARTB', 249, 33, 248, 'PARTC', 249, 75, 248,
                                 'PARTA', 249, 68, 248, 'PARTE', 249, 26, 248, 'TAKARA', 250])

    def test_locked_chest_is_never_targeted(self):
        # Only D placed: B is unlocked, C and A are not (their scripts refuse).
        self.assertTrue(chest_unlocked('PARTB', 0x40))
        self.assertFalse(chest_unlocked('PARTC', 0x40))
        self.assertFalse(chest_unlocked('PARTA', 0x60))
        self.assertTrue(chest_unlocked('PARTA', 0x68))
        # B opened but not placed, nothing carried: must block, not wander to C.
        state = puzzle(248, placed=0x40, chests=(0x80, 0x02))
        self.assertIn('No verified story objective', self.objective_kind(state))

    def fixture(self, name):
        path = ROOT/'fixtures'/name
        if not path.exists():
            self.skipTest(f'{name} unavailable.')
        return decode(path.read_bytes())

    def test_ram_model_room_first_part(self):
        state = self.fixture('ram-249-first-part.bin')
        target, reason = objective(state)
        self.assertEqual((target['triangle'], target['inspect']), (70, True))
        self.assertEqual(target_action(state, target)[0], 'confirm')     # already on the slot triangle
        moved = copy.deepcopy(state)
        moved['player'].update(position=[250, -150, -3], triangle=next(
            t['id'] for t in state['navigation']['mesh'] if t['id'] != 70 and not t['locked']))
        with patch('story.steering', return_value=('right', [0])):
            self.assertNotEqual(target_action(moved, target)[0], 'confirm')

    def test_ram_outer_floor_goes_to_first_chest(self):
        state = self.fixture('ram-248-outer.bin')
        target, reason = objective(state)
        self.assertEqual(target['actor'], 'PARTD')
        self.assertEqual(state['field_checks']['treasures'], [])

    def test_ram_final_part_places_last_slot(self):
        state = self.fixture('ram-249-final-part.bin')
        self.assertEqual(state['mode'], 'dialog')
        self.assertEqual(objective(state), (None, 'Resolve the current dialogue or menu.'))
        state['mode'], state['dialogs'] = 'field', []
        target, _ = objective(state)
        self.assertEqual(target['triangle'], 26)

    def test_live_state_snapshot_final_part(self):
        import json
        path = ROOT/'fixtures/live-state-249-final-part.json'
        if not path.exists():
            self.skipTest('live-state fixture unavailable.')
        state = json.loads(path.read_text())
        state['mode'], state['dialogs'] = 'field', []
        state.setdefault('actors', [])
        target, _ = midgar_model_objective(state)
        if state['key_items'][4] & 0x80:
            self.assertEqual(target['triangle'], 26)


if __name__ == '__main__':
    unittest.main()
