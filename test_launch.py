from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import launch
from emulators import StateError


class LauncherTests(unittest.TestCase):
    def test_boot_preserves_original_save_and_passes_paths_as_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root/'emulator space.exe'
            disc = root/'game space.cue'
            state = root/'savestates/SCUS-94163_resume.sav'
            state.parent.mkdir()
            state.write_bytes(b'original saved game')
            executable.touch()
            disc.touch()
            with patch.object(launch, 'ROOT', root), patch.object(launch.subprocess, 'Popen') as start:
                start.return_value.pid = 10
                self.assertEqual(launch.boot_duckstation(executable, root, disc), 10)
                args = start.call_args.args[0]
                self.assertEqual(args[0], str(executable))
                self.assertEqual(args[-1], str(disc))
                self.assertEqual(args[args.index('-statefile') + 1], str(state))
                backups = list((root/'backup-before-automation').glob('*/SCUS-94163_resume.sav'))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), state.read_bytes())
                self.assertEqual(state.read_bytes(), b'original saved game')

    def test_missing_game_never_starts_emulator(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root/'emu.exe'
            executable.touch()
            with patch.object(launch.subprocess, 'Popen') as start, self.assertRaises(StateError):
                launch.boot_duckstation(executable, root, root/'missing.cue')
            start.assert_not_called()

    def test_running_controller_is_idempotent(self):
        with patch('laya_ui.bot_state', return_value=(True, 'waiting', None)), \
             patch.object(launch.subprocess, 'Popen') as start, patch('builtins.print'):
            launch.main([])
        start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
