"""Local PS1-style control panel for the FF7 Laya bot.

Serves ui/index.html on http://127.0.0.1:7777 (localhost only). It reads the
status/state/decision files the bot already writes and starts/stops the bot
through Start-Laya.ps1 / the STOP file. It never sends input to the game itself.
"""
import ctypes
import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import goals as goal_file

ROOT = Path(__file__).resolve().parent
UI = ROOT / 'ui'
HOST, PORT = '127.0.0.1', int(os.environ.get('LAYA_UI_PORT', '7777'))
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: launching never flashes a console


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default  # file missing or mid-write; next poll picks it up


def pid_alive(pid, image=None):
    """Process exists and is still running; with `image`, its executable
    name must also contain it (guards against a reused PID)."""
    if not pid:
        return False
    if sys.platform != 'win32':
        try:
            os.kill(pid, 0)  # POSIX only; on Windows os.kill would terminate
            return True
        except OSError:
            return False
    from ctypes import wintypes
    k32 = ctypes.WinDLL('kernel32')
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = wintypes.DWORD()
    try:
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
            return False
        if image is None:
            return True
        name, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
        if not k32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
            return False
        return image in os.path.basename(name.value).lower()
    finally:
        k32.CloseHandle(handle)


def bot_state(status):
    """(running, status, error) from the bot's own PID, not DuckStation's."""
    claimed = status.get('status') in ('loading', 'running')
    if not claimed:
        return False, status.get('status', 'idle'), status.get('error')
    if pid_alive(status.get('bot_pid'), image='python'):
        return True, status['status'], status.get('error')
    if not status.get('bot_pid'):
        # Written by an older bot without bot_pid: cannot prove it is running.
        return False, 'stopped', status.get('error')
    return False, 'error', 'Laya stopped without writing a final status (crashed or killed). See laya-errors.log.'


def tail_lines(path, count, chunk=65536):
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - chunk))
            lines = stream.read().decode('utf-8', 'replace').splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-count:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def status_payload():
    status = read_json(ROOT / 'laya-status.json', {}) or {}
    state = read_json(ROOT / 'live-state.json', {}) or {}
    running, bot_status, bot_error = bot_state(status)
    decisions = []
    for d in tail_lines(ROOT / 'decisions.jsonl', 14):
        decisions.append(dict(time=d.get('time'), mode=d.get('mode'), field=d.get('field_id'),
                              action=d.get('applied_action'), reason=d.get('reason'),
                              source=d.get('source'), ms=d.get('inference_ms'), sent=d.get('sent'),
                              run=bool(d.get('run_checks')), end=d.get('macro_end')))
    player = state.get('player') or {}
    return dict(
        running=running,
        stopping=(ROOT / 'STOP').exists() and running,
        status=bot_status if running or status else 'idle',
        attention=status.get('attention') if running or status.get('attention') == 'goal' else None,
        goals=goal_file.summary(state),
        objective=status.get('objective'), reason=status.get('reason'), error=bot_error,
        stop_kind=status.get('mode') if status.get('status') == 'blocked' else None,
        actions_sent=status.get('actions_sent'), median_ms=status.get('median_inference_ms'),
        started=status.get('started'), finished=status.get('finished'),
        target=status.get('target'), execute=status.get('execute'),
        game=state.get('game'), mode=state.get('mode'), field_id=state.get('field_id'),
        field_name=state.get('field_name'), gil=state.get('gil'), party=state.get('party', []),
        story_progress=state.get('story_progress'), disc=state.get('required_disc'),
        position=player.get('position'), decisions=decisions, now=time.time())


def map_payload():
    state = read_json(ROOT / 'live-state.json', {}) or {}
    status = read_json(ROOT / 'laya-status.json', {}) or {}
    mesh = (state.get('navigation') or {}).get('mesh') or []
    player = state.get('player') or {}
    target = status.get('target')
    return dict(
        field_id=state.get('field_id'),
        mesh=[[v[:2] for v in t['vertices']] + [t.get('locked', False)] for t in mesh],
        gateways=[dict(line=[p[:2] for p in g['line']], to=g['destination']) for g in state.get('gateways', [])],
        actors=[dict(name=a['entity_name'], pos=a['position'][:2]) for a in state.get('nearby_actors', [])],
        player=player.get('position', [None, None])[:2], facing=player.get('facing'),
        target=target.get('position', [None, None])[:2] if target and target.get('field_id') == state.get('field_id') else None)


def start_bot(options):
    if status_payload()['running']:
        return 409, dict(ok=False, message='Laya is already running. Stop it first.')
    mode = options.get('mode', 'story')
    args = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-WindowStyle', 'Hidden', '-File', str(ROOT / 'Start-Laya.ps1')]
    if mode == 'observe':
        args.append('-ObserveOnly')
    elif mode == 'assist':
        args.append('-AssistOnly')
    elif mode != 'story':
        return 400, dict(ok=False, message='Unknown mode.')
    if options.get('battle_assist'):
        args.append('-BattleAssist')
    if options.get('walk'):
        args.append('-Walk')
    if options.get('explore'):
        args.append('-Explore')
    try:
        stop_hp = float(options.get('stop_hp') or 0)
    except (TypeError, ValueError):
        return 400, dict(ok=False, message='Stop HP must be a number.')
    if not 0 <= stop_hp <= 100:
        return 400, dict(ok=False, message='Stop HP must be 0 (off) to 100 percent.')
    if stop_hp:
        args += ['-StopAtHp', str(stop_hp)]
    seconds = float(options.get('seconds') or 0)
    if not 0 <= seconds <= 86400:
        return 400, dict(ok=False, message='Duration must be 0 (until stopped) to 86400 seconds.')
    args += ['-Seconds', str(seconds)]
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=30,
                            creationflags=NO_WINDOW if sys.platform == 'win32' else 0)
    if result.returncode:
        return 500, dict(ok=False, message=(result.stderr or result.stdout).strip()[-400:])
    return 200, dict(ok=True, message=result.stdout.strip())


def set_goal(options):
    path = ROOT/'goals.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 500, dict(ok=False, message='goals.json is missing or invalid.')
    goal = next((g for g in data.get('goals', []) if g.get('id') == options.get('id')), None)
    if goal is None or not isinstance(options.get('enabled'), bool):
        return 400, dict(ok=False, message='Unknown goal.')
    goal['enabled'] = options['enabled']
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    os.replace(tmp, path)
    return 200, dict(ok=True, message=f'Goal "{goal["label"]}" {"on" if goal["enabled"] else "off"}.')


def stop_bot():
    # Same effect as Stop-Laya.ps1: the loop checks STOP during every hold.
    (ROOT / 'STOP').write_text('stop', encoding='utf-8')
    return 200, dict(ok=True, message='Stop requested. Controller releases within ~10 ms.')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # pythonw has no console; keep quiet

    def send(self, code, body, kind='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/api/status':
            return self.send(200, status_payload())
        if path == '/api/map':
            return self.send(200, map_payload())
        name = 'index.html' if path in ('/', '') else path.lstrip('/')
        file = (UI / name).resolve()
        if UI not in file.parents or not file.is_file():
            return self.send(404, {'error': 'not found'})
        kind = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css',
                '.svg': 'image/svg+xml', '.png': 'image/png'}.get(file.suffix, 'application/octet-stream')
        self.send(200, file.read_bytes(), kind)

    def do_POST(self):
        # Browsers send Origin on cross-site POSTs; only accept our own page.
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://{HOST}:{PORT}', f'http://localhost:{PORT}'):
            return self.send(403, dict(ok=False, message='Cross-origin request refused.'))
        length = int(self.headers.get('Content-Length') or 0)
        try:
            options = json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            return self.send(400, dict(ok=False, message='Bad JSON.'))
        if self.path == '/api/start':
            return self.send(*start_bot(options))
        if self.path == '/api/goals':
            return self.send(*set_goal(options))
        if self.path == '/api/stop':
            return self.send(*stop_bot())
        self.send(404, {'error': 'not found'})


if __name__ == '__main__':
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
