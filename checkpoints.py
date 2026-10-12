"""Milestone save states: virtual-pad request, verified file, immutable archive.

DuckStation's Save Game State 10 UI currently uses the SaveGlobalState10
settings key. This binding and the USA save header were verified live. No
memory writes, keyboard events or emulator restarts occur.

State loading happens in exactly one case: after a game over (the whole party
at 0 HP in battle), Recovery puts the newest milestone back with the
LoadGlobalState10 hotkey on the virtual pad's right stick button.
"""
from collections import Counter
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import time
import uuid

import goals

ROOT = Path(__file__).resolve().parent
BINDING = 'SDL-0/LeftStick'
HOTKEY = 'SaveGlobalState10'
LOAD_BINDING = 'SDL-0/RightStick'
LOAD_HOTKEY = 'LoadGlobalState10'
RETRIES = 3     # game overs from one milestone before an older one is used
SERIALS = {'SCUS-94163', 'SCUS-94164', 'SCUS-94165'}


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')
        for attempt in range(20):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 19: raise
                time.sleep(.01)
    finally:
        temp.unlink(missing_ok=True)


def enabled(root=ROOT):
    return read_json(Path(root)/'checkpoint-config.json', {}).get('enabled', True) is True


def safe_screen(state):
    return (state.get('game') == 'FF7-USA-verified' and state.get('mode') == 'field'
            and not state.get('movement_locked') and not state.get('fading')
            and any(p.get('hp', 0) > 0 for p in state.get('party', [])))


def adapter_paths(root=ROOT, hotkey=HOTKEY, binding=BINDING, name='Save Game State 10'):
    cfg = read_json(Path(root)/'checkpoint-config.json', {})
    data = Path(cfg.get('duckstation_data', Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData/Local'))/'DuckStation'))
    settings = data/'settings.ini'
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    if not parser.read(settings, encoding='utf-8-sig'):
        raise ValueError('DuckStation settings not found. Set duckstation_data in checkpoint-config.json.')
    if parser.get('Hotkeys', hotkey, fallback='') != binding:
        raise ValueError(f'Bind DuckStation Hotkeys > {name} to {binding} to enable checkpoints.')
    # Do not press a thumb button that could also load a state or affect play.
    section = ''
    for raw in settings.read_text(encoding='utf-8-sig').splitlines():
        line = raw.strip()
        if line.startswith('['): section = line
        elif '=' in line:
            key, value = map(str.strip, line.split('=', 1))
            if binding in value and (section, key) != ('[Hotkeys]', hotkey):
                raise ValueError('Checkpoint button conflicts with another DuckStation binding.')
    folder = Path(parser.get('Folders', 'SaveStates', fallback='savestates'))
    return folder if folder.is_absolute() else data/folder


def validate_save(data):
    if len(data) < 216 or data[:4] != b'DUCC':
        raise ValueError('The emulator did not produce a complete DuckStation save state.')
    serial = data[136:168].split(b'\0', 1)[0].decode('ascii', errors='replace')
    if serial not in SERIALS:
        raise ValueError('Save state is not for the verified USA FF7 discs.')
    size, unpacked, offset = struct.unpack_from('<III', data, 204)
    if not size or not unpacked or unpacked > 32*1024*1024 or offset < 216 or offset+size != len(data):
        raise ValueError('Save state payload is incomplete or its format is unsupported.')
    return serial


def signature(path):
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


class DuckStationSaver:
    def __init__(self, root=ROOT, clock=time.monotonic, sleep=time.sleep):
        self.root, self.clock, self.sleep = Path(root), clock, sleep

    def save(self, state, pad, stopped):
        if state.get('emulator') != 'duckstation':
            raise ValueError('Automatic save states currently support DuckStation only.')
        if not safe_screen(state):
            raise ValueError('Checkpoint waits for an unlocked field screen with a living party.')
        folder = adapter_paths(self.root)
        files = [folder/f'{serial}_10.sav' for serial in sorted(SERIALS)]
        before = {p: signature(p) if p.exists() else None for p in files}
        # Preserve anything in the dedicated slot before asking the emulator
        # to overwrite it. Never delete an earlier checkpoint or user save.
        for path in files:
            if path.exists():
                data = path.read_bytes()
                backup = self.root/'checkpoints'/'slot-backups'/f'{hashlib.sha256(data).hexdigest()}.sav'
                backup.parent.mkdir(parents=True, exist_ok=True)
                if not backup.exists():
                    with backup.open('xb') as stream: stream.write(data)
        if stopped(): raise InterruptedError('Save cancelled by STOP.')
        pad.release()
        try:
            pad.hold('checkpoint', .25, stopped)
        finally:
            pad.release()
        deadline = self.clock()+8
        stable = {}
        while self.clock() < deadline:
            if stopped(): raise InterruptedError('Save cancelled by STOP.')
            for path in files:
                try:
                    stamp = signature(path)
                    if stamp == before[path]: continue
                    if stable.get(path) == stamp:
                        data = path.read_bytes()
                        if signature(path) == stamp:
                            serial = validate_save(data)
                            if path.name != f'{serial}_10.sav':
                                raise ValueError('Save-state filename and game serial disagree.')
                            return data, serial
                    stable[path] = stamp
                except FileNotFoundError:
                    continue
            self.sleep(.15)
        raise TimeoutError('DuckStation did not write a new save state within 8 seconds. Check the save hotkey and save folder.')


class Recovery:
    """After a game over: load a milestone save state and let the run continue."""

    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.deaths = Counter()     # checkpoint file -> times it was loaded after a game over

    def entries(self):
        folder = self.root/'checkpoints'
        return [e for e in read_json(folder/'index.json', []) if (folder/e['file']).is_file()][::-1]

    def choose(self):
        """Newest milestone first; one that keeps ending in a game over yields to an older one."""
        entries = self.entries()
        for entry in entries:
            if self.deaths[entry['file']] < RETRIES:
                return entry
        self.deaths.clear()
        return entries[0] if entries else None

    def restore(self, pad, stopped=lambda: False):
        entry = self.choose()
        if entry is None:
            raise ValueError('Game over and no milestone save state exists yet. Reload a save.')
        folder = adapter_paths(self.root, LOAD_HOTKEY, LOAD_BINDING, 'Load Game State 10')
        data = (self.root/'checkpoints'/entry['file']).read_bytes()
        slot = folder/f'{validate_save(data)}_10.sav'
        current = slot.read_bytes() if slot.exists() else None
        if current != data:
            if current is not None:
                backup = self.root/'checkpoints'/'slot-backups'/f'{hashlib.sha256(current).hexdigest()}.sav'
                backup.parent.mkdir(parents=True, exist_ok=True)
                if not backup.exists():
                    with backup.open('xb') as stream: stream.write(current)
            temp = slot.with_name(slot.name+'.'+uuid.uuid4().hex+'.tmp')
            try:
                temp.write_bytes(data)
                os.replace(temp, slot)
            finally:
                temp.unlink(missing_ok=True)
        if stopped(): raise InterruptedError('Load cancelled by STOP.')
        pad.release()
        try:
            pad.hold('restore', .25, stopped)
        finally:
            pad.release()
        self.deaths[entry['file']] += 1
        return entry


class Milestones:
    def __init__(self):
        self.previous = None
        self.pending = {}

    def reset(self):
        self.previous = None
        self.pending.clear()

    def observe(self, state, goal_defs):
        progress = state.get('story_progress')
        if state.get('game') != 'FF7-USA-verified' or not isinstance(progress, int): return
        done = {g['id']: g['label'] for g in goal_defs if goals.is_done(state, g)}
        current = (progress, done)
        previous, self.previous = self.previous, current
        if previous is None: return  # Attaching to a late save is not a completion.
        if progress < previous[0]:
            self.pending.clear()
            return
        if progress > previous[0]:
            key = f'story-{progress}'
            self.pending[key] = dict(id=key, label=f'Story milestone {progress}', progress=progress)
        for key in done.keys()-previous[1].keys():
            self.pending['goal-'+key] = dict(id='goal-'+key, label=done[key], progress=progress)


class Checkpoints:
    def __init__(self, root=ROOT, saver=None, clock=time.monotonic):
        self.root, self.clock = Path(root), clock
        self.saver = saver or DuckStationSaver(root)
        self.watch = Milestones()
        self.stable_key = None
        self.stable_since = 0
        self.retry_at = 0

    def reset(self):
        self.watch.reset()
        self.stable_key = None

    def report(self, **values):
        write_json(self.root/'checkpoint-status.json', dict(time=time.time(), **values))

    def tick(self, state, pad, stopped=lambda: False):
        """Called by the session-lock owner only. True means resnapshot afterward."""
        if enabled(self.root):
            self.watch.observe(state, goals.load(self.root/'goals.json'))
        else:
            self.watch.reset()
        request = read_json(self.root/'checkpoint-request.json', None)
        request_id = request.get('id') if isinstance(request, dict) else None
        if request and time.time()-request.get('time', 0) > 30:
            request = None
        previous = read_json(self.root/'checkpoint-status.json', {})
        if request_id and request_id == previous.get('request_id') and previous.get('status') in ('saved','error','cancelled'):
            request = None
        if not self.watch.pending and not request: return False
        entries = read_json(self.root/'checkpoints'/'index.json', [])
        completed = {mid for entry in entries if (self.root/'checkpoints'/entry['file']).is_file()
                     for mid in entry.get('milestone_ids', [])}
        milestones = [m for k,m in self.watch.pending.items() if k not in completed]
        for key in completed:
            self.watch.pending.pop(key, None)
        if not milestones and not request: return False
        key = (state.get('field_id'), state.get('story_progress'), state.get('connection_epoch')) if safe_screen(state) else None
        if key is None or key != self.stable_key:
            self.stable_key, self.stable_since = key, self.clock()
        if key is None or self.clock()-self.stable_since < 1 or pad is None:
            if previous.get('status') != 'pending':
                self.report(status='pending', message='Checkpoint queued; waiting for a stable field screen.', request_id=request_id)
            return False
        if self.clock() < self.retry_at: return False
        label = (request.get('label') or 'Manual checkpoint') if request else milestones[-1]['label']
        try:
            if stopped(): raise InterruptedError('Save cancelled by STOP.')
            self.report(status='saving', message=f'Saving {label}…', request_id=request_id)
            data, serial = self.saver.save(state, pad, stopped)
            stamp = time.time()
            slug = re.sub('[^a-z0-9]+', '-', label.lower()).strip('-')[:60] or 'checkpoint'
            name = f'{time.time_ns()}-{slug}.sav'
            folder = self.root/'checkpoints'
            folder.mkdir(parents=True, exist_ok=True)
            with (folder/name).open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            entry = dict(file=name, label=label, saved_at=stamp, serial=serial, bytes=len(data),
                         sha256=hashlib.sha256(data).hexdigest(), milestone_ids=[m['id'] for m in milestones],
                         milestones=milestones, story_progress=state.get('story_progress'),
                         field_id=state.get('field_id'), field_name=state.get('field_name'),
                         disc=state.get('required_disc'), game_seconds=state.get('game_seconds'),
                         party=[{k:p.get(k) for k in ('name','hp','max_hp','mp','max_mp')} for p in state.get('party',[])])
            # Sidecar preserves recovery metadata even if index update fails.
            write_json(folder/(name+'.json'), entry)
            write_json(folder/'index.json', entries+[entry])
            self.watch.pending.clear()
            self.report(status='saved', message=f'Saved {label}.', latest=entry, request_id=request_id)
        except (OSError, ValueError, configparser.Error) as exc:
            self.retry_at = self.clock()+30
            self.report(status='cancelled' if isinstance(exc, InterruptedError) else 'error',
                        message=str(exc), request_id=request_id)
        return True


def summary(root=ROOT):
    root = Path(root)
    entries = read_json(root/'checkpoints'/'index.json', [])
    try:
        adapter_paths(root)
        ready, notice = True, 'Saves after story progress advances and side goals complete.'
    except (OSError, ValueError, configparser.Error) as exc:
        ready, notice = False, str(exc)
    return dict(enabled=enabled(root), ready=ready, notice=notice, directory=str(root/'checkpoints'),
                status=read_json(root/'checkpoint-status.json', {}), count=len(entries),
                recent=[dict(e, available=(root/'checkpoints'/e['file']).is_file()) for e in entries[-5:][::-1]])


def manual_worker():
    """A Save Now request while navigation is stopped uses the same session lock."""
    import msvcrt
    from emulators import processes
    from ff7_state import LiveRAM, StateError
    from laya_loop import Controller
    source = pad = None
    manager = Checkpoints()
    stop = ROOT/'STOP'
    stop_stamp = signature(stop) if stop.exists() else None
    stopped = lambda: (signature(stop) if stop.exists() else None) != stop_stamp
    try:
        with (ROOT/'laya-session.lock').open('a+b') as lock:
            lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
            try: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError: return  # The active bot owns the queued request.
            found = processes()
            if len(found) != 1 or found[0]['emulator'] != 'duckstation':
                raise ValueError('Open exactly one DuckStation instance with USA FF7 to save.')
            source = LiveRAM(found[0]['pid'])
            pad = Controller()
            time.sleep(1.5)
            deadline = time.monotonic()+15
            while time.monotonic() < deadline and not stopped():
                if manager.tick(source.snapshot(attempts=3), pad, stopped): return
                time.sleep(.15)
            raise ValueError('No stable field screen for the checkpoint. Return to the field and try Save Now again.')
    except (OSError, ValueError, StateError) as exc:
        request = read_json(ROOT/'checkpoint-request.json', {})
        manager.report(status='error', message=str(exc), request_id=request.get('id'))
    finally:
        if pad: pad.release()
        if source: source.close()


if __name__ == '__main__':
    manual_worker()
