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

    def test_bad_stop_hp_rejected_without_launching(self):
        for value in (150, -1, 'lots'):
            code, _, args = self.launch(dict(mode='story', stop_hp=value))
            self.assertEqual(code, 400)
            self.assertIsNone(args)


if __name__ == '__main__':
    unittest.main()


class RunningCheckTests(unittest.TestCase):
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
