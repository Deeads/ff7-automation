"""Control panel: start options map onto Start-Laya.ps1 switches (no bot is launched)."""
import unittest
from unittest.mock import patch

import laya_ui


class StartOptionTests(unittest.TestCase):
    def launch(self, options):
        calls = []
        class Done:
            returncode, stdout, stderr = 0, 'started', ''
        with patch.object(laya_ui, 'status_payload', return_value=dict(running=False)), \
             patch.object(laya_ui.subprocess, 'run', side_effect=lambda args, **kw: calls.append(args) or Done()):
            code, body = laya_ui.start_bot(options)
        return code, body, calls[0] if calls else None

    def test_stop_hp_passed_to_launcher(self):
        code, _, args = self.launch(dict(mode='story', battle_assist=True, stop_hp=30))
        self.assertEqual(code, 200)
        self.assertEqual(args[args.index('-StopAtHp')+1], '30.0')
        self.assertIn('-BattleAssist', args)

    def test_stop_hp_off_by_default(self):
        _, _, args = self.launch(dict(mode='story'))
        self.assertNotIn('-StopAtHp', args)

    def test_missing_later_discs_do_not_block_start(self):
        with patch.object(laya_ui.DISC_LIBRARY, 'inventory', return_value=[
                dict(disc=1, available=True), dict(disc=2, available=False), dict(disc=3, available=False)]):
            code, _, args = self.launch(dict(mode='story'))
        self.assertEqual(code, 200)
        self.assertIsNotNone(args)

    def test_bad_stop_hp_rejected_without_launching(self):
        for value in (150, -1, 'lots'):
            code, _, args = self.launch(dict(mode='story', stop_hp=value))
            self.assertEqual(code, 400)
            self.assertIsNone(args)


if __name__ == '__main__':
    unittest.main()


class RunningCheckTests(unittest.TestCase):
    def test_waiting_process_can_still_be_stopped_from_panel(self):
        import os
        self.assertEqual(laya_ui.bot_state(dict(status='waiting', bot_pid=os.getpid()))[:2], (True, 'waiting'))

    def test_own_python_process_counts_as_running(self):
        import os
        self.assertEqual(laya_ui.bot_state(dict(status='running', bot_pid=os.getpid()))[:2], (True, 'running'))

    def test_dead_bot_with_running_status_is_reported_as_crash(self):
        import subprocess, sys
        child = subprocess.run([sys.executable, '-c', 'import os; print(os.getpid())'], capture_output=True, text=True)
        dead = int(child.stdout)
        running, status, error = laya_ui.bot_state(dict(status='running', bot_pid=dead))
        self.assertEqual((running, status), (False, 'error'))
        self.assertIn('without writing a final status', error)

    def test_reused_pid_of_other_program_is_not_the_bot(self):
        import subprocess, sys
        other = subprocess.Popen(['cmd.exe', '/c', 'ping -n 3 127.0.0.1 >nul'],
                                 creationflags=0x08000000 if sys.platform == 'win32' else 0)
        try:
            self.assertFalse(laya_ui.bot_state(dict(status='running', bot_pid=other.pid))[0])
            self.assertTrue(laya_ui.pid_alive(other.pid))            # alive, just not python
        finally:
            other.kill(); other.wait()

    def test_duckstation_pid_alone_no_longer_counts(self):
        import os
        # Old bug: 'pid' is the emulator's PID; a live emulator must not mean a live bot.
        self.assertFalse(laya_ui.bot_state(dict(status='running', pid=os.getpid()))[0])

    def test_finished_states_pass_through(self):
        self.assertEqual(laya_ui.bot_state(dict(status='blocked', error=None))[:2], (False, 'blocked'))

    def test_bot_writes_its_own_pid(self):
        import json, os, tempfile
        from pathlib import Path
        import laya_loop
        class Source:
            pid = 999999
            def snapshot(self, attempts=100): raise laya_loop.StateError('stop here')
            def close(self): pass
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); seen = []
            real = laya_loop.atomic_json
            def spy(path, value):
                seen.append(dict(value)); real(path, value)
            with patch.object(laya_loop, 'ROOT', root), patch.object(laya_loop, 'STOP', root/'STOP'), \
                 patch.object(laya_loop, 'LiveRAM', return_value=Source()), patch.object(laya_loop, 'LayaPolicy'), \
                 patch.object(laya_loop, 'atomic_json', side_effect=spy), patch.object(laya_loop.time, 'sleep'), \
                 patch('builtins.print'):
                try:
                    laya_loop.main(['--seconds', '0'])
                except laya_loop.StateError:
                    pass
        self.assertEqual(seen[0]['bot_pid'], os.getpid())
        self.assertEqual(seen[0]['status'], 'loading')


class DiscPanelTests(unittest.TestCase):
    def payload(self, state, status=None):
        def read(path, default=None):
            return state if path.name == 'live-state.json' else status or {}
        with patch.object(laya_ui, 'read_json', side_effect=read), \
             patch.object(laya_ui, 'tail_lines', return_value=[]), \
             patch.object(laya_ui.goal_file, 'summary', return_value=[]), \
             patch.object(laya_ui.DISC_LIBRARY, 'inventory', return_value=[
                 dict(disc=1, available=True), dict(disc=2, available=False), dict(disc=3, available=False)]):
            return laya_ui.status_payload()

    def test_disc_one_does_not_show_a_later_disc_warning(self):
        payload = self.payload(dict(mode='field', required_disc=1))
        self.assertIsNone(payload['disc_notice'])
        self.assertEqual([d['available'] for d in payload['discs']], [True, False, False])

    def test_missing_disc_named_only_when_an_unsupported_save_requires_it(self):
        self.assertIsNone(self.payload(dict(mode='field', required_disc=2))['disc_notice'])
        self.assertIn('Disc 2', self.payload(dict(mode='unsupported', required_disc=2))['disc_notice'])
        self.assertIn('Disc 3', self.payload({}, dict(disc_needed=3))['disc_notice'])
