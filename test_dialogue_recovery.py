"""Live Aeris escort prompt and recovery without reloading a save."""
import copy
import json
import struct
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import laya_loop
import laya_ui
import route
import story


def prompt():
    return json.loads((Path(__file__).parent/'fixtures/live-dialog-263-aeris.json').read_text())


class EscortTests(unittest.TestCase):
    def test_ko_barret_leaves_tifa_for_boss(self):
        state = prompt()
        self.assertEqual(story.dialogue_action(state)[0], 'down')
        state['dialogs'][0]['choice_row'] = 3
        self.assertEqual(story.dialogue_action(state)[0], 'confirm')

    def test_weaker_tifa_is_sent_instead(self):
        state = prompt()
        state['party'][2]['hp'] = 725
        self.assertEqual(story.dialogue_action(state)[0], 'confirm')

    def test_renamed_party_members(self):
        state = prompt()
        state['party'][2]['name'] = 'Barry'
        state['dialogs'][0]['text'] = state['dialogs'][0]['text'].replace('Barret', 'Barry')
        self.assertEqual(story.dialogue_action(state)[0], 'down')

    def test_unrelated_or_incomplete_prompt_never_confirmed(self):
        for changes in ({'field_id': 262}, {'story_progress': 284}, {'field_name': 'other'}):
            state = prompt(); state.update(changes)
            self.assertIsNone(story.dialogue_action(state)[0])
        state = prompt(); state['dialogs'][0]['text'] = state['dialogs'][0]['text'].replace('Barret, take care of her!', 'Cancel')
        self.assertIsNone(story.dialogue_action(state)[0])
        state = prompt(); state['party'][2]['max_hp'] = 0
        self.assertIsNone(story.dialogue_action(state)[0])

    def test_route_file_update_is_loaded_without_process_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'answers.json'
            path.write_text(json.dumps({'steps': [], 'choices': []}))
            with patch.object(route, 'ROUTES', Path(folder)), patch.object(story, '_table', None), patch.object(story, '_table_version', None):
                self.assertIsNone(story.dialogue_action(prompt())[0])
                choices = [{'field': 263, 'answer': "Barret, take care of her!"}]
                path.write_text(json.dumps({'steps': [], 'choices': choices}))
                self.assertEqual(story.dialogue_action(prompt())[0], 'down')


class RecoveryTests(unittest.TestCase):
    def test_new_recruit_stats_allow_following_dialogue(self):
        from ff7_state import decode
        fixture = Path(__file__).parent/'fixtures/ram-after-red-name.bin'
        if not fixture.exists():
            self.skipTest('Local owned-game RAM capture unavailable.')
        state = decode(fixture.read_bytes())
        self.assertEqual(state['mode'], 'dialog')
        self.assertTrue(state['party'][2]['stats_pending_recalculation'])
        self.assertIn('confirm', laya_loop.choices_for(state, story=True)[0])

    def test_only_full_health_recruit_initialization_uses_base_maxima(self):
        from ff7_state import character_vitals, StateError
        data = bytearray(132)
        struct.pack_into('<4H', data, 0x2C, 731, 731, 125, 125)
        struct.pack_into('<2H', data, 0x38, 217, 17)
        self.assertEqual(character_vitals(data, 0)['max_hp'], 731)
        for hp, base_hp, mp, base_mp in ((732, 731, 125, 125), (731, 731, 124, 125), (10000, 10000, 125, 125)):
            struct.pack_into('<4H', data, 0x2C, hp, base_hp, mp, base_mp)
            with self.assertRaises(StateError):
                character_vitals(data, 0)
        struct.pack_into('<4H', data, 0x2C, 227, 702, 126, 140)
        struct.pack_into('<2H', data, 0x38, 688, 142)
        normal = character_vitals(data, 0)
        self.assertEqual((normal['max_hp'], normal['max_mp']), (688, 142))
        self.assertFalse(normal['stats_pending_recalculation'])

    def test_failed_snapshot_clears_old_naming_handoff(self):
        from ff7_state import StateError
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); statuses = []
            class Source:
                pid = 42
                calls = 0
                def snapshot(self, attempts):
                    self.calls += 1
                    if self.calls > 1:
                        (root/'STOP').write_text('stop')
                        raise StateError('Temporary invalid record')
                    return dict(prompt(), mode='naming')
                def close(self): pass
            real = laya_loop.atomic_json
            def write(path, value):
                if path.name == 'laya-status.json': statuses.append(copy.deepcopy(value))
                return real(path, value)
            with patch.object(laya_loop, 'ROOT', root), patch.object(laya_loop, 'STOP', root/'STOP'), \
                 patch.object(laya_loop, 'SessionRAM', return_value=Source()), \
                 patch.object(laya_loop, 'atomic_json', side_effect=write), \
                 patch.object(laya_loop.time, 'sleep'), patch('builtins.print'):
                laya_loop.main(['--reconnect', '--seconds', '2'])
            failed = next(s for s in statuses if s.get('mode') == 'unsupported')
            self.assertEqual(failed['attention'], 'screen')
            self.assertIsNone(failed['objective'])
            self.assertEqual(json.loads((root/'live-state.json').read_text()), {})

    def test_naming_screen_is_not_cancelled_like_the_main_menu(self):
        from ff7_state import decode
        fixture = Path(__file__).parent/'fixtures/ram-red-name.bin'
        if not fixture.exists():
            self.skipTest('Local owned-game RAM capture unavailable.')
        state = decode(fixture.read_bytes())
        self.assertEqual((state['mode'], state['naming_character']), ('naming', 4))
        choices, reason = laya_loop.choices_for(state, story=True)
        self.assertEqual(set(choices), {'wait'})
        self.assertIn('confirm the name', reason)

    def test_suspended_prompt_resumes_when_verified_answer_becomes_available(self):
        state = prompt()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            statuses = []
            class Source:
                pid = 42
                calls = 0
                def snapshot(self, attempts):
                    self.calls += 1
                    if self.calls > 6:
                        (root/'STOP').write_text('stop')
                    return dict(copy.deepcopy(state), captured_monotonic=time.monotonic())
                def close(self): pass
            source = Source()
            real = laya_loop.atomic_json
            def write(path, value):
                if path.name == 'laya-status.json':
                    statuses.append(copy.deepcopy(value))
                return real(path, value)
            def choose(*args):
                if source.calls <= 3:
                    return {'wait': ''}, 'No verified answer for this dialogue choice.'
                return {'wait': '', 'down': ''}, 'Move to the verified answer.'
            with patch.object(laya_loop, 'ROOT', root), patch.object(laya_loop, 'STOP', root/'STOP'), \
                 patch.object(laya_loop, 'SessionRAM', return_value=source), \
                 patch.object(laya_loop, 'choices_for', side_effect=choose), \
                 patch.object(laya_loop, 'BLOCK_GRACE', 0), \
                 patch.object(laya_loop, 'atomic_json', side_effect=write), \
                 patch.object(laya_loop.time, 'sleep'), patch('builtins.print'):
                laya_loop.main(['--story', '--reconnect', '--seconds', '2'])
            self.assertTrue(any(s.get('attention') == 'dialogue' for s in statuses))
            self.assertTrue(any('Waiting for gameplay' in s.get('reason', '') for s in statuses))
            self.assertTrue(any(s.get('last_decision', {}).get('applied_action') == 'down' for s in statuses))
            self.assertEqual(statuses[-1]['status'], 'stopped')

    def test_panel_returns_current_prompt_text(self):
        text = prompt()['dialogs'][0]['text']
        status = dict(status='waiting', attention='dialogue', dialogue=[text])
        with patch.object(laya_ui, 'read_json', side_effect=lambda path, default: status if path.name == 'laya-status.json' else prompt()), \
             patch.object(laya_ui, 'bot_state', return_value=(True, 'waiting', None)):
            payload = laya_ui.status_payload()
        self.assertEqual(payload['dialogue'], [text])
        self.assertEqual(payload['attention'], 'dialogue')


if __name__ == '__main__':
    unittest.main()
