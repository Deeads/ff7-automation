import hashlib
import unittest
import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import emulators
from emulators import StateError, RAM_SIZE, KERNEL_OFFSET, KERNEL_SIZE
from session import SessionRAM, ConnectionPending, Timeline
from laya_loop import DeterministicPolicy, guard
from test_laya_loop import field
from motion import movement_command


class DiscoveryTests(unittest.TestCase):
    def test_names_and_ambiguity(self):
        self.assertEqual(emulators.emulator_name('duckstation-qt-x64-ReleaseLTCG.exe'), 'duckstation')
        self.assertEqual(emulators.emulator_name('EmuHawk.exe'), 'bizhawk')
        self.assertIsNone(emulators.emulator_name('pcsx2.exe'))
        self.assertIsNone(emulators.emulator_name('not-retroarch.exe'))
        candidates = [dict(pid=1, emulator='duckstation', name='duckstation.exe'),
                      dict(pid=2, emulator='retroarch', name='retroarch.exe')]
        with self.assertRaisesRegex(StateError, 'Multiple'):
            emulators.select_process(candidates)
        self.assertEqual(emulators.select_process(candidates, 'retroarch')['pid'], 2)
        self.assertEqual(emulators.select_process(candidates, pid=17)['pid'], 17)
        with self.assertRaisesRegex(StateError, 'Start the emulator'):
            emulators.select_process([])

    def scan(self, allocations, valid, **kwargs):
        kernel = b'K' * KERNEL_SIZE
        def reader(handle, address, count):
            if count == KERNEL_SIZE:
                return kernel if address - KERNEL_OFFSET in valid else bytes(count)
            if count == RAM_SIZE and address in valid:
                return b'ram'
            raise OSError('unreadable')
        with patch.object(emulators, 'KERNEL_HASH', hashlib.sha256(kernel).hexdigest()):
            return emulators.find_ram(None, allocations, reader=reader, **kwargs)

    def test_mapping_start_and_embedded_heap(self):
        self.assertEqual(self.scan([(0x100000, RAM_SIZE)], {0x100000}), [0x100000])
        self.assertEqual(self.scan([(0x100000, RAM_SIZE + 0x3000)], {0x102000}), [0x102000])

    def test_custom_unaligned_base_still_verifies(self):
        self.assertEqual(self.scan([], {0x123456}, base=0x123456), [0x123456])
        with self.assertRaisesRegex(StateError, 'verified FF7'):
            self.scan([], set(), base=0x123456)

    def test_scan_budget_and_stop(self):
        with self.assertRaisesRegex(StateError, 'budget'):
            self.scan([(0, RAM_SIZE * 8)], set(), max_scan_bytes=4096)
        with self.assertRaisesRegex(StateError, 'stopped'):
            self.scan([(0, RAM_SIZE)], set(), stopped=lambda: True)

    def test_divergent_frozen_copies_are_not_guessed(self):
        self.assertEqual(emulators.live_mappings(None, [1, 2],
                         reader=lambda h, b, n: bytes([b]), sleep=lambda n: None), [1, 2])


class SessionTests(unittest.TestCase):
    def test_disconnect_reconnect_and_throttled_retry(self):
        now = [0]
        class Source:
            pid = 15
            closed = False
            broken = False
            def snapshot(self, attempts):
                if self.broken:
                    raise OSError('process exited')
                return field()
            def close(self): self.closed = True
        first, second = Source(), Source()
        with patch('session.LiveRAM'):
            from unittest.mock import Mock
            factory = Mock(side_effect=[StateError('not loaded'), first, second])
            session = SessionRAM(factory=factory, clock=lambda: now[0])
            with self.assertRaises(ConnectionPending): session.snapshot()
            with self.assertRaises(ConnectionPending): session.snapshot()
            self.assertEqual(factory.call_count, 1)
            now[0] = 3
            self.assertEqual(session.snapshot()['connection_epoch'], 1)
            first.broken = True
            with self.assertRaises(ConnectionPending): session.snapshot()
            self.assertTrue(first.closed)
            now[0] = 6
            self.assertEqual(session.snapshot()['connection_epoch'], 2)
            session.close()
            self.assertTrue(second.closed)

    def test_rewind_forward_save_and_reconnect(self):
        clock = [0]
        timeline = Timeline(clock=lambda: clock[0])
        def state(seconds, progress=278, epoch=1):
            return dict(game_seconds=seconds, story_progress=progress, connection_epoch=epoch)
        self.assertFalse(timeline.observe(state(100)))
        clock[0] += 1
        self.assertFalse(timeline.observe(state(101)))
        self.assertTrue(timeline.observe(state(90)))
        self.assertTrue(timeline.observe(state(800)))
        self.assertTrue(timeline.observe(state(800, epoch=2)))

    def test_movement_and_guard_reject_changed_connection(self):
        before = field()
        after = dict(before, connection_epoch=2)
        self.assertIn('changed', guard(before, after, 'cancel', 2))
        self.assertIsNone(movement_command(before, after, {})[0])
        self.assertIsNone(movement_command(dict(before, game_seconds=100),
                                         dict(before, game_seconds=50), {})[0])


class PolicyTests(unittest.TestCase):
    def test_only_unique_action_is_selected(self):
        policy = DeterministicPolicy()
        for choices, expected in (({'wait': '', 'confirm': ''}, 'confirm'),
                                  ({'wait': ''}, 'wait'),
                                  ({'wait': '', 'up': '', 'down': ''}, 'wait')):
            self.assertEqual(policy.choose({}, choices, None, [])['action'], expected)

    def test_fresh_battle_guard_retains_escape_attempt_history(self):
        before = field()
        before.update(mode='battle', flee_exhausted=True, battle_target_open=False,
                      battle=dict(id=1, menu_state=1, escape_difficulty=1,
                                  players=[dict(alive=True, hp=1, max_hp=100)],
                                  enemies=[dict(alive=True, targetable=True)]))
        after = copy.deepcopy(before)
        del after['flee_exhausted']
        del after['battle_target_open']
        self.assertIsNone(guard(before, after, 'confirm', 2, battle_assist=True))
        self.assertNotIn('flee_exhausted', after)

    def test_fresh_battle_guard_retains_pending_target_cancel(self):
        before = field()
        before.update(mode='battle', flee_exhausted=False, battle_target_open=True,
                      battle=dict(id=1, menu_state=1, escape_difficulty=9,
                                  players=[dict(alive=True, hp=100, max_hp=100)],
                                  enemies=[dict(alive=True, targetable=False)]))
        after = copy.deepcopy(before)
        del after['battle_target_open']
        self.assertIsNone(guard(before, after, 'cancel', 2, battle_assist=True))
        after['battle']['id'] = 2
        self.assertIsNotNone(guard(before, after, 'cancel', 2, battle_assist=True))


class RecoveryLoopTests(unittest.TestCase):
    def test_wait_then_reconnect_then_stop_without_a_model(self):
        import laya_loop
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            statuses = []
            class Source:
                pid = 42
                calls = 0
                closed = False
                def snapshot(self, attempts):
                    self.calls += 1
                    if self.calls == 1:
                        raise ConnectionPending('Waiting for FF7')
                    if self.calls == 3:
                        (root/'STOP').write_text('stop')
                    return field()
                def close(self): self.closed = True
            source = Source()
            real = laya_loop.atomic_json
            def write(path, value):
                if path.name == 'laya-status.json':
                    statuses.append(dict(value))
                return real(path, value)
            with patch.object(laya_loop, 'ROOT', root), patch.object(laya_loop, 'STOP', root/'STOP'), \
                 patch.object(laya_loop, 'SessionRAM', return_value=source), \
                 patch.object(laya_loop, 'LayaPolicy', side_effect=AssertionError('Must not load model')), \
                 patch.object(laya_loop, 'atomic_json', side_effect=write), \
                 patch.object(laya_loop.time, 'sleep'), patch('builtins.print'):
                laya_loop.main(['--reconnect', '--seconds', '2'])
            self.assertIn('waiting', [s['status'] for s in statuses])
            self.assertIn('running', [s['status'] for s in statuses])
            self.assertEqual(statuses[-1]['status'], 'stopped')
            self.assertEqual(statuses[-1]['actions_sent'], 0)
            self.assertTrue(source.closed)


if __name__ == '__main__':
    unittest.main()
