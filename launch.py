"""One-command FF7 startup and read-only diagnostics (Windows)."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from emulators import processes, StateError
from ff7_state import LiveRAM

ROOT = Path(__file__).resolve().parent
NO_WINDOW = 0x08000000


def installation():
    local = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
    return (local / 'Programs/DuckStation/duckstation-qt-x64-ReleaseLTCG.exe',
            local / 'DuckStation',
            Path.home() / 'Downloads/gam/Final Fantasy VII (USA) (Disc 1).cue')


def driver_installed():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Services\ViGEmBus'):
            return True
    except FileNotFoundError:
        return False


def diagnose():
    found = processes()
    report = dict(emulators=found, python=sys.executable,
                  controller_package=importlib.util.find_spec('vgamepad') is not None,
                  controller_driver=driver_installed(),
                  verified_scope='FF7 USA Disc 1 field/dialog/menu/battle; incomplete story coverage',
                  limitations=['Other emulator layouts are experimental.',
                               'World map, minigames, arbitrary dialogue choices and automatic disc swaps are not implemented.'])
    from story_catalog import StoryCatalog
    catalog = StoryCatalog()
    catalog._load()
    report['story_catalogue'] = dict(path=str(catalog.path), entries=sum(map(len, catalog.rows.values())),
                                   fields=len(catalog.rows), error=catalog.error,
                                   validation='Each target is checked against live field state; catalogue coverage is not verified playthrough coverage.')
    if len(found) == 1:
        source = None
        try:
            source = LiveRAM(found[0]['pid'])
            state = source.snapshot(attempts=3)
            report['game'] = {k: state.get(k) for k in ('mode', 'field_id', 'field_name', 'story_progress', 'required_disc')}
            report['ram_base'] = hex(source.base)
        except (StateError, OSError) as exc:
            report['waiting_for'] = str(exc)
        finally:
            if source:
                source.close()
    else:
        report['waiting_for'] = 'Start one emulator and load a USA FF7 save.' if not found else 'Close extra emulator instances.'
    return report


def boot_duckstation(executable, data_dir, disc, save_state=None):
    """Called only when no emulator is open. Back up owned save files first."""
    if not executable.is_file():
        raise StateError(f'DuckStation not found at {executable}. Supply --emulator-path.')
    if not disc.is_file():
        raise StateError(f'Game image not found at {disc}. Supply --disc with your own game image.')
    state = save_state or data_dir / 'savestates/SCUS-94163_resume.sav'
    if save_state and not state.is_file():
        raise StateError(f'Save state not found: {state}')
    args = [str(executable), '-nofullscreen']
    if state.is_file():
        # Unique directory; repeated starts never replace earlier backups.
        backup = ROOT / 'backup-before-automation' / f'{time.time_ns()}'
        backup.mkdir(parents=True)
        shutil.copy2(state, backup / state.name)
        for card in (data_dir / 'memcards').glob('*Final Fantasy VII*.mcd'):
            shutil.copy2(card, backup / card.name)
        args += ['-statefile', str(state)]
    args += ['--', str(disc)]
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return subprocess.Popen(args, cwd=executable.parent, startupinfo=startup, creationflags=NO_WINDOW).pid


def main(argv=None):
    exe, data, disc = installation()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--doctor', action='store_true', help='Report readiness without starting anything or pressing buttons.')
    parser.add_argument('--observe', action='store_true', help='Start without controller input.')
    parser.add_argument('--panel', action='store_true', help='Only make sure the emulator is running; the control panel starts the bot.')
    parser.add_argument('--seconds', type=float, default=0)
    parser.add_argument('--emulator-path', type=Path, default=exe)
    parser.add_argument('--data-dir', type=Path, default=data)
    parser.add_argument('--disc', type=Path, default=disc)
    parser.add_argument('--save-state', type=Path)
    args = parser.parse_args(argv)
    if args.doctor:
        print(json.dumps(diagnose(), indent=2))
        return
    import math
    if not math.isfinite(args.seconds) or args.seconds < 0:
        parser.error('--seconds must be finite and nonnegative.')
    from laya_ui import bot_state, read_json
    running = bot_state(read_json(ROOT / 'laya-status.json', {}) or {})[0]
    # A reconnecting bot outlives the emulator: the panel start must still boot the game.
    if running and not args.panel:
        print('FF7 controller is already running. Use Stop-Laya.ps1 to stop it.')
        return
    found = processes()
    if len(found) > 1:
        raise StateError('Multiple emulators are open. Close extras before starting.')
    if not args.observe and (importlib.util.find_spec('vgamepad') is None or not driver_installed()):
        raise StateError('Install requirements.txt and the ViGEmBus driver before controller playback. Run --doctor for readiness.')
    if not found:
        pid = boot_duckstation(args.emulator_path, args.data_dir, args.disc, args.save_state)
        print(f'DuckStation starting (PID {pid}); saved files backed up before loading a resume state.')
    if args.panel:
        return
    # Do not pin the PID: reconnect must discover the next emulator after exit.
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    command = [str(pythonw if pythonw.exists() else Path(sys.executable)), str(ROOT / 'laya_loop.py'),
               '--story', '--reconnect', '--battle-assist', '--seconds', str(args.seconds)]
    if not args.observe:
        command.append('--execute')
    with (ROOT / 'laya-output.log').open('w') as out, (ROOT / 'laya-errors.log').open('w') as err:
        process = subprocess.Popen(command, cwd=ROOT, stdout=out, stderr=err, creationflags=NO_WINDOW)
    print(f'FF7 controller starting (PID {process.pid}). Status: {ROOT / "laya-status.json"}')
    print('Basic battle assist is enabled. Unsupported choices and modes wait for your input; full-game play is not implemented.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, StateError) as exc:
        print(f'Startup: {exc}', file=sys.stderr)
        sys.exit(1)
