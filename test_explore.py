"""Fallback exploration uses the field's own script triggers, never guesses."""
from collections import Counter
import json
import unittest
from pathlib import Path

import field_script
from explore import Explorer, with_exploration

ROOT = Path(__file__).resolve().parent


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def state_with(field, evt, position=(1077.0, 122.0, 0.0), actors=()):
    base = json.loads((ROOT/'fixtures'/'blin66_3-state.json').read_text())
    triggers = field_script.story_triggers((ROOT/'fixtures'/evt).read_bytes())
    # A flat test mesh covering the whole field keeps every trigger walkable.
    mesh = [dict(id=0, vertices=[[-3000, -3000, 0], [3000, -3000, 0], [0, 3000, 0]], neighbors=[-1, -1, -1], locked=False)]
    return dict(base, field_id=field, story_progress=273, mode='field', movement_locked=False,
                field_triggers=triggers, actors=list(actors), navigation=dict(base['navigation'], mesh=mesh),
                player=dict(base['player'], position=list(position), triangle=0))


class ExploreTests(unittest.TestCase):
    def test_scan_finds_the_hojo_corner_trigger(self):
        t = field_script.story_triggers((ROOT/'fixtures'/'blin67_1.evt').read_bytes())
        sjp = next(l for l in t['lines'] if l['name'] == 'sjp')
        self.assertTrue(sjp['jumps'])
        self.assertEqual(sjp['line'], [[-861, -567, 0], [-696, -640, 0]])

    def test_no_route_step_tries_map_changing_trigger_first(self):
        explorer = Explorer(clock=Clock())
        state = state_with(256, 'blin67_1.evt')
        target, reason = with_exploration(explorer, state, None, 'No verified story objective for field 256, progress 273.')
        self.assertTrue(reason.startswith('Exploring'))
        self.assertEqual(target['gateway'], -1)
        self.assertIn(target['explore'][1], (11, 18))  # sjp or elevator line, both change map

    def test_budget_moves_on_and_then_searches_again(self):
        clock = Clock()
        explorer = Explorer(clock=clock)
        state = state_with(256, 'blin67_1.evt')
        seen = []
        for _ in range(200):
            target, reason = explorer.target(state)
            if explorer.sweeps:
                break
            seen.append(target['explore'])
            clock.t += 500       # past any walking allowance
        # Within one search triggers are tried once; only exits are reopened, a bounded number of times.
        counts = Counter(seen)
        self.assertTrue(all(n == 1 for cid, n in counts.items() if cid[0] != 'gateway'))
        self.assertTrue(all(n <= 4 for n in counts.values()))
        # It never parks: everything is forgotten and the search starts over.
        self.assertEqual(target['explore'], seen[0])
        self.assertIn('Search 2', reason)

    def test_parks_only_when_the_field_offers_nothing(self):
        state = state_with(256, 'blin67_1.evt')
        state['field_triggers'], state['gateways'] = {}, []
        target, reason = Explorer(clock=Clock()).target(state)
        self.assertIsNone(target)
        self.assertTrue(reason.startswith('Explored every story trigger'))

    def test_script_that_advances_the_story_now_goes_first(self):
        state = state_with(256, 'blin67_1.evt')
        explorer = Explorer(clock=Clock())
        usual = explorer.target(state)[0]['explore']
        door = next(l for l in state['field_triggers']['lines'] if ('line', l['entity']) != usual and not l['ok'])
        door.update(advances=True, gates=[state['story_progress']], keys=True)
        target, reason = Explorer(clock=Clock()).target(state)
        self.assertEqual(target['explore'], ('press', door['entity']))      # stand on it and press Circle
        self.assertTrue(target['ok_line'])
        door.update(gates=[state['story_progress']+50], keys=False)         # gated on another chapter: no priority
        self.assertEqual(Explorer(clock=Clock()).target(state)[0]['explore'], usual)

    def test_story_use_reads_gates_buttons_and_progress_writes(self):
        # eleout lin0 (live 2026-10-12): IFUB, IFKEYON, IFUW progress == 311, BATTLE, SETWORD progress.
        ops = [(0x14, bytes.fromhex('500900008c')), (0x31, bytes.fromhex('200288')),
               (0x16, bytes.fromhex('20000037010036')), (0x70, bytes.fromhex('00cc01')),
               (0x81, bytes.fromhex('20003a01')), (0x00, b'')]
        self.assertEqual(field_script.story_use({4: ops}), dict(gates=[311], advances=True, keys=True))
        self.assertEqual(field_script.story_use({2: ops[:2]}), dict(gates=[], advances=False, keys=False))

    def test_objective_mode_skips_door_lines(self):
        explorer = Explorer(clock=Clock())
        names = {c[2][1] for c in explorer.candidates(state_with(256, 'blin67_1.evt'))}
        self.assertNotIn(14, names)  # dr1: door auto-open line
        self.assertIn(14, {c[2][1] for c in Explorer(everything=True).candidates(state_with(256, 'blin67_1.evt'))})

    def test_route_step_wins_unless_explore_mode(self):
        state = state_with(252, 'blin66_3.evt', actors=[dict(entity=3, entity_name='pipe', talkable=True, position=[0, 0, 0],
                                                            triangle=0, collision_radius=10, distance=50)])
        route_target = dict(field_id=252, position=[1, 2, 3], radius=12)
        self.assertIs(with_exploration(Explorer(), state, route_target, 'x')[0], route_target)
        talk, why = with_exploration(Explorer(everything=True), state, route_target, 'x')
        self.assertEqual(talk['entity'], 3)
        self.assertIn('talk to pipe', why)

    def test_fallback_uses_reachable_ordinary_exits(self):
        state = state_with(252, 'blin66_3.evt')
        state['field_triggers'] = {}
        state['gateways'] = [dict(id=0, destination=253, line=[[0, 0, 0], [10, 0, 0]])]
        target, _ = Explorer().target(state)
        self.assertEqual(target['destination'], 253)
        self.assertEqual(target['explore'], ('gateway', 0))

    def test_round_trip_does_not_repeat_consumed_exit(self):
        explorer = Explorer(clock=Clock())
        first = state_with(252, 'blin66_3.evt')
        first['field_triggers'] = {}
        first['gateways'] = [dict(id=0, destination=253, line=[[0, 0, 0], [10, 0, 0]])]
        second = dict(first, field_id=253,
                      gateways=[dict(id=0, destination=252, line=[[0, 0, 0], [10, 0, 0]])])
        # Roaming between two dead ends is allowed a few rounds, then it stops.
        for _ in range(4):               # the first pass plus ROAM_ROUNDS reopenings
            self.assertIsNotNone(explorer.target(first)[0])
            self.assertIsNotNone(explorer.target(second)[0])
        self.assertEqual(explorer.sweeps, 0)
        self.assertIsNotNone(explorer.target(first)[0])      # then a new search, not a stop
        self.assertEqual(explorer.sweeps, 1)
        # A real story milestone makes the field worth examining again.
        self.assertIsNotNone(explorer.target(dict(first, story_progress=274))[0])


if __name__ == '__main__':
    unittest.main()
