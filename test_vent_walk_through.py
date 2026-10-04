"""The blin66_3 vent LINE fires from its move script, so the route walks through it."""
import json
import unittest
from pathlib import Path

import route
import story

ROOT = Path(__file__).resolve().parent


class VentWalkThrough(unittest.TestCase):
    def setUp(self):
        route._passes.clear()
        story._table = None
        self.state = json.loads((ROOT/'fixtures'/'blin66_3-state.json').read_text())

    def at(self, x, y):
        state = dict(self.state, player=dict(self.state['player'], position=[x, y, -376.0]))
        return state

    def test_vent_step_walks_instead_of_pressing_circle(self):
        target, label = story.objective(self.at(74.688, 134.312))
        self.assertTrue(target['walk_through'])
        self.assertNotIn('inspect', target)
        self.assertIn('vent', label.lower())
        action, reason = story.target_action(self.at(74.688, 134.312), target)
        self.assertIn(action, ('left', 'right', 'up', 'down', 'up_left', 'up_right', 'down_left', 'down_right'))

    def test_ping_pong_switches_goal_on_arrival_and_caps(self):
        first, _ = story.objective(self.at(74.688, 134.312))
        other, _ = story.objective(self.at(*first['position'][:2]))
        self.assertNotEqual(first['position'], other['position'])
        state = self.at(*other['position'][:2])
        goal = other
        for _ in range(10):
            goal, reason = story.objective(self.at(*goal['position'][:2]))
            if goal is None:
                break
        self.assertIsNone(goal)
        self.assertIn('without the event starting', reason)

    def test_vent_line_is_in_move_script_not_ok(self):
        import field_script as fs
        section = (ROOT/'fixtures'/'blin66_3.evt').read_bytes()
        parsed = fs.parse_section(section)
        line = parsed['tables'][2]
        ops = lambda i: [op for _, op, _ in fs.instructions(section, line[i], line[i+1] if line[i+1] > line[i] else parsed['code_end'])]
        self.assertEqual(ops(2), [0x00])      # [OK]: just RET
        self.assertIn(0x48, ops(3))           # ASK "Climb up" lives in the move script


if __name__ == '__main__':
    unittest.main()
