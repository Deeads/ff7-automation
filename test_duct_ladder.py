"""The blin66_4 duct is a player-controlled LADER: the bot must press, not wait."""
import unittest
from pathlib import Path

import ff7_state
import laya_loop
import motion
import story

ROOT = Path(__file__).resolve().parent


class DuctLadder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = ff7_state.decode((ROOT/'fixtures'/'ram-blin66_4-duct.bin').read_bytes())

    def test_ladder_is_not_a_script_lock(self):
        self.assertEqual(self.state['field_id'], 253)
        self.assertEqual(self.state['player']['animation_state'], 4)
        self.assertFalse(self.state['movement_locked'])
        # Live-verified: state 4 / switch 0 advances on D-pad Down; Up backs out.
        self.assertEqual(self.state['player']['ladder']['forward'], 'down')
        self.assertEqual(self.state['player']['ladder']['destination'][:2], [448, -536])

    def test_route_presses_the_live_forward_key(self):
        story._table = None
        target, label = story.objective(self.state)
        self.assertEqual(target['hold'], 'down')           # route's old guess
        choices, reason = laya_loop.choices_for(self.state, target, story=True)
        self.assertIn('down', choices)
        self.assertNotIn('up', choices)                    # Up would back out to the vent
        action, running, _ = motion.movement_command(self.state, self.state, target)
        self.assertEqual((action, running), ('down', False))


    def test_cutscene_is_not_blocked(self):
        import inspect
        source = inspect.getsource(laya_loop.main)
        self.assertIn('not cutscene and reason.startswith', source)

if __name__ == '__main__':
    unittest.main()
