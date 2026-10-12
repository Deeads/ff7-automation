import copy
import json
from pathlib import Path
import time
import unittest
import tempfile
from unittest.mock import patch

from ff7_state import decode, StateError, fftext
from laya_loop import choices_for, decision_from, guard, Controller
import laya_loop


def field():
    return dict(pid=12, captured_monotonic=time.monotonic(), mode='field', field_id=242,
        field_state=0, module_request=1, story_progress=264, party=[], dialogs=[],
        movement_locked=False, fading=False, player={'position': [100, 100, 0]},
        navigation={'direction_vectors': {'up': [0, 1], 'down': [0, -1], 'left': [-1, 0], 'right': [1, 0]}})


class StateTests(unittest.TestCase):
    def test_wrong_game_memory_rejected(self):
        with self.assertRaises(StateError):
            decode(bytes(0x200000))

    def test_real_observed_ff7_fixture(self):
        path = Path(__file__).parent/'ram-candidate-1.bin'
        if not path.exists():
            self.skipTest('Local observed RAM fixture unavailable.')
        state = decode(path.read_bytes())
        self.assertEqual(state['field_id'], 242)
        self.assertEqual([p['name'] for p in state['party']], ['Cloud', 'Tifa', 'Barret'])
        self.assertEqual(state['party'][0]['hp'], 556)
        self.assertEqual(state['mode'], 'menu')

    def test_text_terminator(self):
        self.assertEqual(fftext(bytes([35, 76, 79, 85, 68, 255, 0])), 'Cloud')


class ChoiceTests(unittest.TestCase):
    def test_unknown_mode_has_no_input(self):
        state = field(); state['mode'] = 'unsupported'
        self.assertEqual(set(choices_for(state)[0]), {'wait'})

    def test_field_without_route_waits(self):
        self.assertEqual(set(choices_for(field())[0]), {'wait'})

    def test_wrong_field_route_waits(self):
        self.assertEqual(set(choices_for(field(), {'field_id': 243, 'position': [150, 100]})[0]), {'wait'})

    def test_target_filters_irrelevant_directions(self):
        target = {'field_id': 242, 'position': [150, 100.1]}
        self.assertEqual(set(choices_for(field(), target)[0]), {'wait', 'right'})

    def test_locked_movement_waits(self):
        state = field(); state['movement_locked'] = True
        self.assertEqual(set(choices_for(state, {'field_id': 242, 'position': [150, 100]})[0]), {'wait'})

    def test_dialog_choice_not_auto_confirmed(self):
        state = field(); state.update(mode='dialog', dialogs=[{'choice_pointer': True}])
        self.assertEqual(set(choices_for(state)[0]), {'wait'})

    def test_completed_dialog_advanced(self):
        state = field(); state.update(mode='dialog', dialogs=[{'choice_pointer': False, 'ready': True, 'auto_close': False}])
        self.assertIn('confirm', choices_for(state)[0])

    def test_confidence_and_option_validation(self):
        choices = {'wait': '', 'cancel': ''}
        answer = {'choice': 'cancel', 'probabilities': {'wait': 0.2, 'cancel': 0.8}, 'answer_confidence': 0.8}
        self.assertEqual(decision_from({'answers': {'action': answer}}, choices)['action'], 'cancel')
        for override in ({'choice': 'save'}, {'probabilities': {'wait': float('nan'), 'cancel': 0.8}},
                         {'probabilities': {'wait': 0.8, 'cancel': 0.8}}, {'answer_confidence': 0.2}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                decision_from({'answers': {'action': dict(answer, **override)}}, choices)

    def test_stale_decision_rejected(self):
        state = field(); state['captured_monotonic'] -= 10
        self.assertIsNotNone(guard(state, state, 'wait', 2))

    def test_changed_state_rejected(self):
        before = field(); after = copy.deepcopy(before); after['story_progress'] += 1
        self.assertIsNotNone(guard(before, after, 'wait', 2))

    def test_no_longer_legal_action_rejected(self):
        state = field()
        self.assertIsNotNone(guard(state, state, 'right', 2))


class ControllerTests(unittest.TestCase):
    def test_navigation_failure_is_visible_instead_of_objective_label(self):
        state = json.loads((Path(__file__).parent/'fixtures/live-state-256-unreachable.json').read_text())
        from route import gateway_near
        target = gateway_near(state, [1185, -142, 224])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            statuses = []
            class Source:
                pid = 12
                def snapshot(self, attempts=100): return copy.deepcopy(state)
                def close(self): pass
            real_write = laya_loop.atomic_json
            def write(path, value):
                if path.name == 'laya-status.json':
                    statuses.append(copy.deepcopy(value))
                    if value.get('attention') == 'route':
                        (root/'STOP').write_text('stop')
                return real_write(path, value)
            with patch.object(laya_loop, 'ROOT', root), patch.object(laya_loop, 'STOP', root/'STOP'), \
                 patch.object(laya_loop, 'LiveRAM', return_value=Source()), \
                 patch.object(laya_loop, 'objective', return_value=(target, 'Enter the laboratory.')), \
                 patch.object(laya_loop, 'atomic_json', side_effect=write), patch('builtins.print'):
                laya_loop.main(['--story', '--no-collect', '--seconds', '2'])
            waiting = [s for s in statuses if s['status'] == 'waiting']
            self.assertTrue(waiting)
            self.assertTrue(waiting[0]['reason'].startswith('Navigation blocked:'))
            self.assertEqual(waiting[0]['objective'], 'Enter the laboratory.')

    def test_unknown_story_route_stops_and_releases_controller(self):
        class Source:
            pid = 12
            closed = False
            def snapshot(self, attempts=100): return field()
            def close(self): self.closed = True
        class Pad:
            released = False
            def perform(self, action, **kwargs):
                self.asserted_action = action
                if action != 'wait': raise AssertionError('Unknown route must not press a button')
                return dict(sent=False)
            def release(self): self.released = True
        source, pad = Source(), Pad()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(laya_loop,'ROOT',root), patch.object(laya_loop,'STOP',root/'STOP'), \
                 patch.object(laya_loop,'LiveRAM',return_value=source), patch.object(laya_loop,'Controller',return_value=pad), \
                 patch.object(laya_loop,'LayaPolicy'), patch.object(laya_loop.time,'sleep'), \
                 patch.object(laya_loop,'objective',return_value=(None,'No verified story objective for this field.')), \
                 patch('builtins.print'):
                laya_loop.main(['--story','--execute','--seconds','0'])
            status = json.loads((root/'laya-status.json').read_text())
            self.assertEqual(status['status'],'blocked')
            self.assertEqual(status['actions_sent'],0)
            self.assertTrue(status['controller_released'])
            self.assertTrue(source.closed)
            self.assertTrue(pad.released)

    def test_error_releases_pressed_button(self):
        class FakePad:
            released = False
            def press_button(self, **kwargs):
                raise OSError('Disconnected')
            def reset(self):
                self.released = True
            def update(self):
                pass
        ctrl = Controller.__new__(Controller)
        ctrl.pad = FakePad(); ctrl.buttons = {'confirm': 'B'}
        ctrl.user32 = type('Window', (), {'GetForegroundWindow': lambda self: 12})()
        with self.assertRaises(OSError):
            ctrl.perform('confirm')
        self.assertTrue(ctrl.pad.released)

    def test_stop_prevents_press(self):
        class FakePad:
            def reset(self):
                pass
            def update(self):
                pass
            def press_button(self, **kwargs):
                raise AssertionError('Stop must prevent button input')
        ctrl = Controller.__new__(Controller); ctrl.pad = FakePad()
        ctrl.user32 = type('Window', (), {'GetForegroundWindow': lambda self: 12})()
        self.assertFalse(ctrl.perform('confirm', stopped=lambda: True)['sent'])


if __name__ == '__main__':
    unittest.main()
