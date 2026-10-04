"""Fallback exploration uses the field's own script triggers, never guesses."""
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

    def test_budget_moves_on_and_finally_gives_up(self):
        clock = Clock()
        explorer = Explorer(clock=clock)
        state = state_with(256, 'blin67_1.evt')
        seen = []
        for _ in range(40):
            target, reason = explorer.target(state)
            if target is None:
                break
            seen.append(target['explore'])
            clock.t += 13
        self.assertIsNone(target)
        self.assertTrue(reason.startswith('Explored every story trigger'))
        self.assertEqual(len(set(seen)), len(seen))

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


if __name__ == '__main__':
    unittest.main()
