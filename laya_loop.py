"""Local FF7 state -> Laya choice -> persistent background gamepad loop."""
import argparse
import ctypes
from ctypes import wintypes
import json
import math
import msvcrt
import os
from pathlib import Path
import statistics
import time

from ff7_state import LiveRAM, StateError, DIRECTION_ANGLES, party_hp, party_health
from navigation import steering, NoPath
from story import objective, dialogue_action, target_action, ChoiceGuesser, DialogNudge, ScreenNudge
from battle import battle_choices
from motion import drive, stalled, interact
from collect import Collector, with_collection
from explore import Explorer, with_exploration
from goals import GoalWatch
from battle import validate_players
from battle_control import BattleControl, valid as valid_battle_plan
from battle_strategy import Action as BattleAction
from emulators import EMULATORS, processes
from session import SessionRAM, ConnectionPending, Timeline
from story_catalog import StoryCatalog
from discs import DiscLibrary
from checkpoints import Checkpoints, Recovery
from field_heal import FieldHeal
from supplies import Supplies
from grind import Atlas, Retreat, Grind
import route

ROOT = Path(__file__).resolve().parent
STOP = ROOT / 'STOP'
ACTIONS = frozenset(('wait', 'cancel', 'confirm', 'flee', 'switch', 'start', 'square', *DIRECTION_ANGLES))


def atomic_json(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    # Windows refuses the replace while another program (the control panel,
    # an editor, a diagnostics read) has the file open. That is harmless for
    # a status file: retry briefly, then skip this write instead of crashing
    # the run with the controller in use.
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(0.01)
    return False


def choices_for(state, target=None, story=False, battle_assist=False):
    choices = {'wait': 'Wait with all controller buttons released.'}
    mode = state['mode']
    if mode == 'battle':
        return battle_choices(state, battle_assist)
    if mode == 'naming':
        return choices, 'Character naming screen: confirm the name in FF7. Automation resumes after this screen closes.'
    heal = state.get('heal')
    if heal and mode in ('field', 'menu'):
        # Recovery outranks routes and closing the menu until the party is restored.
        if heal['input']:
            choices[heal['input']] = heal['reason']
        return choices, heal['reason']
    if mode == 'menu':
        choices['cancel'] = 'Press Cross once to close the current menu and return to gameplay.'
    elif mode == 'dialog':
        dialogs = state['dialogs']
        if any(d['choice_pointer'] for d in dialogs):
            if story or state.get('dialogue_answer'):
                action, reason = dialogue_action(state)
                if action:
                    choices[action] = reason
                return choices, reason
            return choices, 'Dialogue choice needs a story-specific plan.'
        if any(d['ready'] and not d['auto_close'] for d in dialogs):
            choices['confirm'] = 'Press Circle once to advance the fully displayed dialogue.'
        elif state.get('dialog_nudge'):
            choices[state['dialog_nudge']] = 'This message does not close by itself: press the button it asks for.'
    elif mode == 'field':
        if state['movement_locked'] or state['fading']:
            return choices, 'Waiting for scripted movement or a scene transition.'
        if not target or target['field_id'] != state['field_id']:
            return choices, 'Awaiting a route target for this field.'
        player = state['player']['position']
        distance = math.dist(player[:2], target['position'][:2])
        if (distance <= target.get('radius', 12) and not target.get('actor') and not target.get('inspect')
                and target.get('gateway') is None and not target.get('hold')):
            return choices, 'Route target reached.'
        if 'mesh' in state['navigation']:
            try:
                action, reason = target_action(state, target)
            except NoPath as exc:
                # Wait and replan next tick; repeated failure trips the
                # no-progress stop instead of crashing with input state unknown.
                return choices, f'Navigation blocked: {exc}'
            if action:
                choices[action] = reason
            return choices, reason
        for direction, vector in state['navigation']['direction_vectors'].items():
            dot = sum((target['position'][i]-player[i])*vector[i] for i in range(2))
            if dot / distance > 0.25:
                choices[direction] = f'Walk {direction} toward the target.'
    else:
        if state.get('screen_nudge'):
            kind = (state.get('field_state'), state.get('module_request'))
            choices[state['screen_nudge']] = ('Bike chase: swing the sword left and right.' if kind == ScreenNudge.BIKE
                                              else 'Party screen: put three members in the party.' if kind == ScreenNudge.PARTY
                                              else 'Unknown screen has not changed: press Cross to back out of it.')
            return choices, choices[state['screen_nudge']]
        return choices, 'Unsupported game mode: battle, loading, world map, or other screen.'
    return choices, 'Ready' if len(choices) > 1 else 'Waiting'


def log_battle(state, source, memo, capture=False):
    """Raw + decoded battle records about once a second, with the independent
    save-record check, so decode_battle can be validated against the screen."""
    now = time.monotonic()
    if now-memo['last'] < 1.0:
        return
    memo['last'] = now
    battle = state['battle']
    entry = dict(time=time.time(), battle_id=battle['id'], players=battle['players'], enemies=battle['enemies'],
                 raw=battle['raw'], escape=battle['escape_difficulty'], limit_mask=battle['limit_mask'],
                 party_save=[(p['name'], p['hp'], p['max_hp']) for p in state['party']],
                 validation_problems=validate_players(battle, state['party']))
    if capture and now-memo['capture'] >= 5.0:
        memo['capture'] = now
        folder = ROOT/'battle-captures'
        folder.mkdir(exist_ok=True)
        path = folder/f'{int(time.time()*1000)}.png'
        try:
            import window_capture
            window_capture.save(source.pid, path)
            entry['capture'] = path.name
        except OSError as exc:
            entry['capture_error'] = str(exc)
    with (ROOT/'battle-log.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(entry)+'\n')


POST_BATTLE_PRESS = 0.7
POST_BATTLE_LIMIT = 60.0
GAME_OVER_RETRY = 12.0   # seconds between load attempts after a game over


def low_party(members, percent):
    """First (name, hp, max) under `percent` of max HP (KO included), or None."""
    if not percent:
        return None
    return next(((name, hp, top) for name, hp, top in members if top and hp*100 < percent*top), None)


def state_health(state):
    """Battle HP while fighting (save records lag behind), save HP otherwise."""
    names = [p['name'] for p in state.get('party', [])]
    if state['mode'] == 'battle' and state.get('battle'):
        return [(names[i] if i < len(names) else f'slot {p["slot"]}', p['hp'], p['max_hp'])
                for i, p in enumerate(state['battle']['players'])]
    return [(p['name'], p['hp'], p['max_hp']) for p in state.get('party', [])]


def advance_post_battle(source, pad, memo, stopped):
    """EXP/AP, Gil/items and level-up screens after a battle: press Circle
    until a supported screen returns. Game over (all save HP 0) presses nothing."""
    now = time.monotonic()
    memo['since'] = memo['since'] or now
    try:
        hp = party_hp(source.raw())
    except (OSError, StateError):
        return 'Waiting for readable post-battle memory; controller released.'
    if not hp:
        return 'Waiting for a loaded party; controller released.'
    if hp and not any(hp):
        return 'game_over'
    if now-memo['since'] > POST_BATTLE_LIMIT:
        raise StateError('Post-battle screens did not return to gameplay within 60 s.')
    if pad and now-memo['pressed'] >= POST_BATTLE_PRESS and not stopped():
        pad.perform('confirm', stopped=stopped)
        memo['pressed'] = time.monotonic()
        memo['presses'] += 1
    time.sleep(0.05)
    return 'Advancing post-battle results with Circle.'


def route_key(state, target, objective_reason):
    """Identity of a movement objective. A wandering NPC's position is not
    part of it, so the executor keeps following without re-asking Laya."""
    position = None if target.get('actor') else tuple(round(n) for n in target['position'][:2])
    return (state['field_id'], objective_reason, target.get('gateway'), target.get('actor'),
            target.get('triangle'), position)


def decision_from(result, choices):
    try:
        answer = result['answers']['action']
        choice = answer['choice']
        probabilities = answer['probabilities']
        if choice not in choices or choice not in ACTIONS or set(probabilities) != set(choices):
            raise ValueError('Choice or probability keys differ from allowed actions.')
        if any(isinstance(p, bool) or not isinstance(p, (float, int)) or not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities.values()):
            raise ValueError('Invalid probabilities.')
        if abs(sum(probabilities.values()) - 1) > 0.02:
            raise ValueError('Probabilities do not sum to one.')
        if probabilities[choice] < max(probabilities.values()) - 0.0002:
            raise ValueError('Selected choice is not the highest probability.')
        confidence = answer.get('answer_confidence', probabilities[choice])
        if not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or abs(confidence-probabilities[choice]) > 0.02:
            raise ValueError('Confidence does not match selected probability.')
        return dict(action=choice, probability=probabilities[choice], probabilities=probabilities,
                    model_confidence=answer.get('confidence'))
    except (KeyError, TypeError) as exc:
        raise ValueError('Malformed Laya decision.') from exc


def guard(before, after, action, max_age, target=None, story=False, battle_assist=False):
    if time.monotonic() - before['captured_monotonic'] > max_age:
        return 'State expired while Laya was deciding.'
    if after.get('game_seconds', 0) < before.get('game_seconds', 0):
        return 'Save timeline changed before input.'
    for key in ('pid', 'connection_epoch', 'mode', 'field_id', 'field_state', 'module_request', 'story_progress', 'party', 'menu'):
        if before.get(key) != after.get(key):
            return 'Game state changed while Laya was deciding.'
    if 'heal' in before:
        after = dict(after, heal=before['heal'])
    if 'dialogue_answer' in before:
        after = dict(after, dialogue_answer=before['dialogue_answer'])
    if 'dialog_nudge' in before:
        after = dict(after, dialog_nudge=before['dialog_nudge'])
    if 'screen_nudge' in before:
        after = dict(after, screen_nudge=before['screen_nudge'])
    if before.get('dialogs') != after.get('dialogs') or before.get('player') != after.get('player'):
        return 'Player or dialogue changed while Laya was deciding.'
    if before.get('key_items') != after.get('key_items'):
        return 'Story inventory changed while Laya was deciding.'
    if before.get('battle') and (before['battle']['id'], before['battle']['menu_state']) != (after.get('battle', {}).get('id'), after.get('battle', {}).get('menu_state')):
        return 'Battle or command menu changed while Laya was deciding.'
    if before.get('battle'):
        if before['battle'].get('menu') != after.get('battle', {}).get('menu'):
            return 'Battle cursor or active character changed before input.'
        if before['battle'].get('planned_action'):
            if not valid_battle_plan(BattleAction(**before['battle']['planned_action']), after):
                return 'Battle plan is no longer valid.'
        after = dict(after, battle=dict(after['battle'], **{k: before['battle'][k] for k in
                     ('control_input', 'control_reason', 'planned_action') if k in before['battle']}))
        # These flags are controller-session facts, not RAM fields. Rechecking
        # fresh RAM must retain them or every attack after two failed escape
        # attempts is rejected because the fresh snapshot offers escape again.
        after = dict(after, **{k: before[k] for k in ('flee_exhausted', 'battle_target_open') if k in before})
    if action not in choices_for(after, target, story, battle_assist)[0]:
        return 'Action is no longer allowed.'
    return None


class DeterministicPolicy:
    """Execute the sole proposed action; ambiguous proposals stay released.

    A score of 1 is a deterministic decision, not a gameplay success estimate.
    The same fresh-state guard applies as for a model-selected action.
    """
    def choose(self, state, choices, target, history):
        actions = [a for a in choices if a != 'wait']
        action = actions[0] if len(actions) == 1 else 'wait'
        return dict(action=action, probability=1,
                    probabilities={a: int(a == action) for a in choices}, model_confidence=None)


def recovery_key(state):
    """Observable changes that justify retrying a blocked objective.

    Excludes capture times, animation frames and facing, which can change
    forever while Cloud is stuck against a wall.
    """
    return json.dumps({k: state.get(k) for k in
                       ('connection_epoch', 'mode', 'field_id', 'story_progress',
                        'key_items', 'puzzles', 'dialogs', 'menu')}, sort_keys=True) + str(
        tuple(round(v / 16) for v in (state.get('player') or {}).get('position', [])))


class LayaPolicy:
    def __init__(self, threads=4):
        os.environ['HF_HOME'] = str(ROOT/'models')
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
        import torch
        import laya
        torch.set_num_threads(threads)
        snapshots = ROOT/'models/hub/models--convaiinnovations--laya/snapshots'
        path = snapshots/'55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851'
        if not (path/'model.safetensors').exists():
            raise RuntimeError('The verified local Laya model is missing.')
        self.agent = laya.load(str(path), device='cpu')

    def choose(self, state, choices, target, history):
        # Keep the full adapter JSON on disk, but give the small decision model
        # only facts relevant to this choice. Raw geometry consumed its context
        # and materially increased CPU latency in the live benchmark.
        context = {'mode': state['mode']}
        if state['mode'] == 'battle':
            context.update(goal=next((v for k,v in choices.items() if k != 'wait'), 'Wait.'),
                           party_hp=[(p['hp'],p['max_hp']) for p in state['battle']['players']])
        elif state['mode'] == 'menu':
            context['goal'] = next((v for k,v in choices.items() if k != 'wait'), 'Wait for the menu.')
        elif state['mode'] == 'dialog':
            context.update(goal=next((v for k,v in choices.items() if k != 'wait'), 'Advance the completed dialogue.'),
                           dialogue_finished=True,
                           choice_menu=any(d['choice_pointer'] for d in state['dialogs']))
        else:
            directions = ', '.join(a for a in choices if a != 'wait')
            context.update(goal=next((v for k,v in choices.items() if k != 'wait'), 'Move toward the nearby target.'),
                           target_direction=directions, movement_enabled=True)
        result = self.agent.system_one(context, {'action': dict(type='choice',
            instructions='Choose the next action to achieve the goal.',
            criteria=choices)})
        return decision_from(result, choices)


RELEASE_EDGE = 0.10
ALIASES = dict(cross='cancel', circle='confirm', run='cancel')


class Controller:
    # Button state of the last report sent; Cross (Xbox A) is run in the field
    # and cancel everywhere else, so its release time gates every confirm.
    held_mask = 0
    cross_released_at = -math.inf
    buttons = {}

    def __init__(self):
        import vgamepad as vg
        self.vg = vg
        self.pad = vg.VX360Gamepad()
        self.buttons = {name: getattr(vg.XUSB_BUTTON, 'XUSB_GAMEPAD_'+button) for name, button in
            dict(cancel='A', confirm='B', switch='Y', square='X', start='START', up='DPAD_UP', down='DPAD_DOWN', left='DPAD_LEFT', right='DPAD_RIGHT').items()}
        self.buttons['flee'] = vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER | vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER
        self.buttons['checkpoint'] = vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB
        self.buttons['restore'] = vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB
        for name in DIRECTION_ANGLES:
            if '_' in name:
                vertical, horizontal = name.split('_')
                self.buttons[name] = self.buttons[vertical] | self.buttons[horizontal]
        self.user32 = ctypes.WinDLL('user32')
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.release()

    def _write(self, mask):
        # One report per call: reset + press + update is a single pad state,
        # so a direction change never passes through a released report.
        self.pad.reset()
        if mask:
            self.pad.press_button(button=mask)
        self.pad.update()
        cross = self.buttons.get('cancel', 0)
        if self.held_mask & cross and not mask & cross:
            self.cross_released_at = time.perf_counter()
        self.held_mask = mask

    def release(self):
        self._write(0)

    def mask(self, buttons):
        """'cross+up' / 'circle' / 'release' -> one button mask."""
        if buttons in ('release', '', None):
            return 0
        mask = 0
        for name in buttons.split('+'):
            name = ALIASES.get(name.strip(), name.strip())
            if name not in self.buttons:
                raise ValueError(f'Unknown controller button {name!r}.')
            mask |= self.buttons[name]
        return mask

    def release_edge(self, stopped=lambda: False):
        """Fully release (Cross = cancel outside movement), then wait one edge."""
        if self.held_mask:
            self.release()
        while time.perf_counter() < self.cross_released_at+RELEASE_EDGE and not stopped():
            time.sleep(0.01)

    def set_movement(self, action, running):
        if action not in DIRECTION_ANGLES:
            raise ValueError('Only movement directions may be held with Run.')
        self._write(self.buttons[action] | (self.buttons['cancel'] if running else 0))

    def macro(self, sequence, stopped=lambda: False):
        """Run [(buttons, seconds), ...] without releasing between steps.

        Steps pressing Circle first release Cross and wait one release edge.
        Always released in `finally`; STOP is checked every 10 ms.
        """
        foreground = self.user32.GetForegroundWindow()
        started = time.monotonic()
        held = cross_held = 0.0
        changes, last_direction, sent = 0, None, False
        directions = 0
        for name in ('up', 'down', 'left', 'right'):
            directions |= self.buttons.get(name, 0)
        end = 'Completed.'
        try:
            for buttons, seconds in sequence:
                mask = self.mask(buttons)
                if mask & self.buttons.get('confirm', 0) and not mask & self.buttons.get('cancel', 0):
                    self.release_edge(stopped)
                if stopped():
                    end = 'STOP requested or session ended.'
                    break
                self._write(mask)
                sent = sent or bool(mask)
                if mask & directions:
                    changes += int(last_direction is not None and mask & directions != last_direction)
                    last_direction = mask & directions
                step_start = time.monotonic()
                until = step_start+seconds
                while time.monotonic() < until and not stopped():
                    time.sleep(0.01)
                elapsed = time.monotonic()-step_start
                if mask:
                    held += elapsed
                    cross_held += elapsed if mask & self.buttons.get('cancel', 0) else 0
                if stopped():
                    end = 'STOP requested or session ended.'
                    break
        except Exception as exc:
            end = f'Error: {type(exc).__name__}: {exc}'
            raise
        finally:
            self.release()
        return dict(sent=sent, foreground_unchanged=self.user32.GetForegroundWindow() == foreground,
                    macro='sequence', steps=len(sequence), duration_s=round(time.monotonic()-started, 3),
                    held_s=round(held, 3), cross_held_pct=round(100*cross_held/held, 1) if held else 0.0,
                    direction_changes=changes, macro_end=end)

    def hold(self, buttons, seconds, stopped=lambda: False):
        return self.macro([(buttons, seconds)], stopped)

    def perform(self, action, duration=0.12, stopped=lambda: False):
        if action not in ACTIONS:
            raise ValueError('Disallowed controller action.')
        foreground = self.user32.GetForegroundWindow()
        if action == 'wait' or stopped():
            self.release()
            return dict(sent=False, foreground_unchanged=True)
        if action != 'cancel':
            # Cross held for running means cancel in dialogue/menus: release
            # it and give the game one clean edge before any other press.
            self.release_edge(stopped)
            if stopped():
                return dict(sent=False, foreground_unchanged=True)
        try:
            self._write(self.buttons[action])
            end = time.monotonic()+duration
            while time.monotonic() < end and not stopped():
                time.sleep(0.01)
        finally:
            self.release()
        # Ensure consecutive confirmations have a visible release edge.
        end = time.monotonic()+0.10
        while time.monotonic() < end and not stopped():
            time.sleep(0.01)
        return dict(sent=True, foreground_unchanged=self.user32.GetForegroundWindow() == foreground)


def live_fight(state):
    """A battle still in progress: someone on each side is standing."""
    battle = state.get('battle') or {}
    return (any(u.get('alive') for u in battle.get('players', []))
            and any(u.get('alive') for u in battle.get('enemies', [])))


BLOCK_GRACE = 20.0  # seconds with no state change before 'no objective' stops the run
REACHED_GRACE = 5.0   # seconds on a reached waypoint whose step is still unfinished
UNSURE_AFTER = 6    # inputs with no change before an objective is dropped for exploration
RETRY_STALL = 15.0  # seconds before a stalled run tries its movement again


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Enable virtual-controller output; otherwise observe only.')
    parser.add_argument('--seconds', type=float, default=30, help='Run duration after model load; 0 means until STOP.')
    parser.add_argument('--pid', type=int)
    parser.add_argument('--emulator', choices=['auto', *EMULATORS], default='auto')
    parser.add_argument('--ram-base', type=lambda s: int(s, 0), help='Explicit host RAM address; still fingerprint-checked.')
    parser.add_argument('--reconnect', action='store_true', help='Wait for emulator/game loading and reconnect after process loss.')
    parser.add_argument('--policy', choices=['deterministic', 'laya'], default='deterministic',
                        help='Default needs no model or network; Laya is optional.')
    parser.add_argument('--interval', type=float, default=0.125, help='Minimum time between decisions, not a promised rate.')
    parser.add_argument('--max-state-age', type=float, default=2.0)
    parser.add_argument('--min-probability', type=float, default=0.60)
    parser.add_argument('--target', type=float, nargs=3, metavar=('FIELD', 'X', 'Y'))
    parser.add_argument('--story', action='store_true', help='Follow implemented story objectives from current save flags.')
    parser.add_argument('--stop-hp', type=float, default=0, metavar='PERCENT',
                        help='Stop (pad released) when any party member is below this %% of max HP; 0 = off.')
    parser.add_argument('--heal-hp', type=float, default=50, metavar='PERCENT',
                        help='Between battles, revive and heal from the main menu when anyone is KO or below this %% of max HP; 0 = off.')
    parser.add_argument('--grind', action='store_true',
                        help='Test loop: seek random battles in the nearest encounter field (implies --battle-assist).')
    parser.add_argument('--retreat-mp', type=float, default=0, metavar='PERCENT',
                        help='Go to the nearest verified rest spot when the best healer is below this %% of max MP '
                             '(always when under two Cures, or when someone is hurt and nothing can heal them).')
    parser.add_argument('--ask-choices', action='store_true',
                        help='Wait for you at dialogue choices without a verified answer instead of trying each option in turn.')
    parser.add_argument('--battle-probe', action='store_true',
                        help='Once per battle, probe command/target cursor bytes (harmless menu moves, backs out).')
    parser.add_argument('--capture', action='store_true',
                        help='Save DuckStation window captures during battles (PrintWindow; no focus change).')
    parser.add_argument('--no-collect', dest='collect', action='store_false',
                        help='Do not open reachable chests/pickups before leaving a field (default: collect).')
    parser.add_argument('--explore', action='store_true',
                        help='Also talk to everyone once per field and try door triggers (default: objectives only).')
    parser.add_argument('--walk', action='store_true', help='Disable the held Cross/run modifier during field movement.')
    parser.add_argument('--battle-assist', action='store_true', help='Experimental basic confirm/escape support; not a complete combat strategy.')
    args = parser.parse_args(argv)
    if args.pid is not None and args.pid <= 0:
        parser.error('--pid must be positive.')
    if args.ram_base is not None and args.ram_base < 0:
        parser.error('--ram-base cannot be negative.')
    values = (args.seconds, args.interval, args.max_state_age, args.min_probability)
    if not math.isfinite(args.stop_hp) or not 0 <= args.stop_hp <= 100:
        parser.error('--stop-hp must be a percentage between 0 and 100.')
    if not math.isfinite(args.heal_hp) or not 0 <= args.heal_hp <= 100:
        parser.error('--heal-hp must be a percentage between 0 and 100.')
    if not math.isfinite(args.retreat_mp) or not 0 <= args.retreat_mp <= 100:
        parser.error('--retreat-mp must be a percentage between 0 and 100.')
    args.battle_assist = args.battle_assist or args.grind
    if not all(math.isfinite(n) for n in values) or args.seconds < 0 or args.interval < 0.05 or not 0 < args.max_state_age <= 5 or not 0 <= args.min_probability <= 1:
        parser.error('Invalid duration, interval, maximum state age, or probability.')
    target = None
    if args.target:
        field, x, y = args.target
        if not all(math.isfinite(n) for n in args.target) or field != int(field) or not 0 <= field <= 1000 or max(abs(x), abs(y)) > 32767:
            parser.error('Invalid field/coordinates.')
        target = dict(field_id=int(field), position=[x, y], radius=12)
    # A Windows file lock is released by the OS after crashes. A second launch
    # cannot clear STOP or create another virtual controller.
    with (ROOT/'laya-session.lock').open('a+b') as lock:
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('Laya is already running; use Stop-Laya.ps1 first.')
        STOP.unlink(missing_ok=True)
        source = pad = None
        timings, history = [], []
        sent = 0
        started = time.time()
        # bot_pid is this process (the panel checks it); 'pid' later is DuckStation's.
        status = dict(status='loading', execute=args.execute, started=started, target=target,
                      bot_pid=os.getpid())
        atomic_json(ROOT/'laya-status.json', status)
        try:
            deadline = time.monotonic()+args.seconds if args.seconds else math.inf
            stopped = lambda: STOP.exists() or time.monotonic() >= deadline
            if args.reconnect:
                source = SessionRAM(args.pid, args.emulator, args.ram_base, stopped)
            elif args.emulator == 'auto' and args.ram_base is None:
                source = LiveRAM(args.pid)
            else:
                source = LiveRAM(args.pid, emulator=args.emulator, base=args.ram_base, stopped=stopped)
            policy = LayaPolicy() if args.policy == 'laya' else DeterministicPolicy()
            status['policy'] = args.policy
            if STOP.exists():
                return
            if args.execute:
                pad = Controller()
                # SDL needs time to discover the single persistent pad.
                for _ in range(150):
                    if STOP.exists():
                        return
                    time.sleep(0.01)
            unchanged_actions = 0
            stale_steps = {}      # (field, progress) -> route step ids that had no effect there
            blocked_since = blocked_progress = None
            last_progress = None
            approved_route = None
            collector = Collector() if args.story and args.collect else None
            explorer = Explorer(everything=args.explore) if args.story else None
            catalog = StoryCatalog() if args.story else None
            goal_watch = GoalWatch() if args.story and args.execute else None
            battle_log = dict(last=0.0, capture=0.0, probes={}, flees={}, seen={})
            battle_control = BattleControl()
            field_heal = FieldHeal(args.heal_hp/100)
            # Fetching needs story navigation and something to do with the item.
            roaming = (args.story or args.grind) and args.execute
            supplies = Supplies(ROOT) if roaming and args.heal_hp else None
            atlas = Atlas(ROOT)
            retreat = Retreat(atlas, args.heal_hp/100, args.retreat_mp/100) if roaming and args.heal_hp else None
            grind = Grind(atlas) if roaming and args.grind else None
            guesser = ChoiceGuesser() if args.story and args.execute and not args.ask_choices else None
            nudger = DialogNudge() if args.execute else None
            screen_nudger = ScreenNudge() if args.execute else None
            checkpoints = Checkpoints(ROOT)
            read_failures = 0
            last_mode = None
            post_battle = dict(since=None, pressed=0.0, presses=0)
            recovery = Recovery(ROOT)
            wipe = dict(seen=False, pressed=0.0, alive_since=None)

            def game_over():
                """The whole party fell: load the last milestone instead of waiting for someone."""
                reason = 'Game over: every party member has 0 HP.'
                if pad and time.monotonic()-wipe['pressed'] >= GAME_OVER_RETRY and not stopped():
                    wipe['pressed'] = time.monotonic()
                    try:
                        entry = recovery.restore(pad, stopped)
                        reason += f' Loading {entry["label"]}.'
                        event = dict(event='game_over_reload', checkpoint=entry['file'], label=entry['label'],
                                     attempt=recovery.deaths[entry['file']])
                    except (OSError, ValueError) as exc:
                        reason += f' {exc}'
                        event = dict(event='game_over_blocked', reason=str(exc))
                    wipe['blocked'] = event['event'] == 'game_over_blocked'
                    with (ROOT/'decisions.jsonl').open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps(dict(time=time.time(), **event))+'\n')
                    wipe['note'] = reason
                pad and pad.release()
                status.update(status='waiting', mode='game_over', attention='screen', target=None,
                              reason=wipe.get('note', reason))
                atomic_json(ROOT/'laya-status.json', status)
            timeline = Timeline()
            disc_library = DiscLibrary(ROOT)
            suspended = None
            reached_since = None
            last_guess = None
            suspended_at = 0.0
            deaf_stalls = 0
            input_check_at = 0
            input_conflict = False
            while time.monotonic() < deadline and not STOP.exists():
                tick = time.monotonic()
                # A virtual pad is broadcast to applications, not scoped to PID.
                if pad and tick >= input_check_at:
                    input_check_at = tick + 1
                    input_conflict = len(processes()) > 1
                if input_conflict:
                    pad.release()
                    status.update(status='waiting', attention='connection', target=None,
                                  reason='Multiple emulators are open. Close extras before controller input can resume.')
                    atomic_json(ROOT/'laya-status.json', status)
                    time.sleep(0.1)
                    continue
                after_battle = last_mode in ('battle', 'post_battle')
                try:
                    # Right after a battle, don't spend 5 s retrying a layout
                    # that the results screens simply do not have.
                    state = source.snapshot(attempts=3 if after_battle or args.reconnect else 100)
                    read_failures = 0
                except ConnectionPending as exc:
                    pad and pad.release()
                    approved_route = None
                    last_mode = None
                    post_battle.update(since=None, pressed=0.0, presses=0)
                    status.update(status='waiting', mode='disconnected', attention='connection',
                                  reason=str(exc), target=None)
                    atomic_json(ROOT/'laya-status.json', status)
                    atomic_json(ROOT/'live-state.json', {})
                    time.sleep(0.1)
                    continue
                except StateError as exc:
                    if wipe['seen']:
                        game_over()
                        atomic_json(ROOT/'live-state.json', {})
                        time.sleep(0.1)
                        continue
                    if not after_battle:
                        # Transition/unknown screens: wait with the pad released.
                        read_failures += 1
                        if read_failures > 12 and not args.reconnect:
                            raise
                        pad and pad.release()
                        status.update(status='waiting' if args.reconnect else 'running', mode='unsupported',
                                      target=None, objective=None, dialogue=[], attention='screen',
                                      reason=f'Waiting for a supported screen: {exc}')
                        try:
                            # This save byte indicates the story's required disc;
                            # it is not proof of a visible swap prompt.
                            data = getattr(source, 'raw', lambda: b'')()
                            from emulators import verified, KERNEL_OFFSET, KERNEL_SIZE
                            from ff7_state import SAVE
                            needed = data[SAVE+0xEA4] if verified(data[KERNEL_OFFSET:KERNEL_OFFSET+KERNEL_SIZE]) else None
                            status['disc_needed'] = needed if needed in (2, 3) else None
                            notice = disc_library.needed_message(needed)
                            if notice:
                                status.update(attention='disc', reason=notice)
                        except (StateError, OSError):
                            status['disc_needed'] = None
                        atomic_json(ROOT/'laya-status.json', status)
                        atomic_json(ROOT/'live-state.json', {})
                        time.sleep(0.1)
                        continue
                    state = None
                if state is not None and timeline.observe(state):
                    pad and pad.release()
                    approved_route = suspended = blocked_since = last_progress = None
                    unchanged_actions = 0
                    history.clear()
                    route.reset_runtime()
                    collector = Collector() if args.story and args.collect else None
                    explorer = Explorer(everything=args.explore) if args.story else None
                    catalog = StoryCatalog() if args.story else None
                    goal_watch = GoalWatch() if args.story and args.execute else None
                    battle_log = dict(last=0.0, capture=0.0, probes={}, flees={}, seen={})
                    battle_control.reset()
                    field_heal.reset()
                    supplies and supplies.reset()
                    retreat and retreat.reset()
                    grind and grind.reset()
                    checkpoints.reset()
                    post_battle.update(since=None, pressed=0.0, presses=0)
                    wipe.update(seen=False, alive_since=None)
                    after_battle = False
                    last_mode = None
                    with (ROOT/'decisions.jsonl').open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps(dict(time=time.time(), event='timeline_reset',
                                                     field_id=state['field_id']))+'\n')
                if state is not None:
                    status['disc_needed'] = state.get('required_disc') if state['mode'] == 'unsupported' and state.get('required_disc') in (2, 3) else None
                # Low-HP stop: checked before anything is pressed this tick.
                try:
                    health = state_health(state) if state else party_health(source.raw())
                except (OSError, StateError):
                    health = []
                # Recovery the bot can still perform comes before handing over.
                healing = bool(pad and state is not None and field_heal.prepare(state))
                if supplies and supplies.searching(state):
                    # Someone is KO and a Phoenix Down is being fetched for them.
                    health = [member for member in health if member[1] > 0]
                low = None if healing else low_party(health, args.stop_hp)
                if low:
                    name, hp, top = low
                    pad and pad.release()
                    status.update(status='blocked', mode='low_hp', party_hp=health,
                                  reason=f'Stopped: {name} at {hp}/{top} HP ({100*hp/top:.0f}%) is below {args.stop_hp:g}%. Take over.')
                    with (ROOT/'decisions.jsonl').open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps(dict(time=time.time(), event='low_hp_stop', member=name, hp=hp,
                                                     max_hp=top, threshold=args.stop_hp)) + '\n')
                    break
                if state is not None and state['mode'] == 'battle' and state.get('battle'):
                    fighters = state['battle'].get('players') or []
                    if fighters and not any(p.get('hp', 0) > 0 for p in fighters):
                        wipe['seen'] = True
                    wipe['alive_since'] = None
                elif wipe['seen'] and state is not None and state['mode'] in ('field', 'dialog') \
                        and any(p.get('hp', 0) > 0 for p in state.get('party', [])):
                    # Back in play (loaded state). A single stale frame does not count.
                    wipe['alive_since'] = wipe['alive_since'] or time.monotonic()
                    if time.monotonic()-wipe['alive_since'] >= 3:
                        wipe.update(seen=False, alive_since=None)
                        wipe.pop('note', None)
                        last_mode = None
                        after_battle = False
                if wipe['seen'] and (state is None or state['mode'] not in ('battle', 'field', 'dialog')):
                    game_over()
                    time.sleep(0.1)
                    continue
                if after_battle and (state is None or state['mode'] not in ('battle', 'field', 'dialog', 'menu', 'naming')):
                    # EXP/Gil/level-up screens: advance them instead of stalling.
                    last_mode = 'post_battle'
                    try:
                        outcome = advance_post_battle(source, pad, post_battle, stopped)
                    except StateError as exc:
                        if not args.reconnect:
                            raise
                        pad and pad.release()
                        status.update(status='waiting', mode='unsupported', attention='screen', reason=str(exc), target=None)
                        atomic_json(ROOT/'laya-status.json', status)
                        time.sleep(0.1)
                        continue
                    if outcome == 'game_over':
                        wipe['seen'] = True
                        game_over()
                        if wipe.get('blocked') and not args.reconnect:
                            # Nothing to load and nobody reconnecting: hand over.
                            status.update(status='blocked')
                            break
                        continue
                    status.update(status='running', mode='post_battle', reason=outcome)
                    atomic_json(ROOT/'laya-status.json', status)
                    continue
                if last_mode == 'post_battle':
                    with (ROOT/'decisions.jsonl').open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps(dict(time=time.time(), event='post_battle_done',
                                                     presses=post_battle['presses'], back_to=state['mode']))+'\n')
                    post_battle.update(since=None, presses=0)
                if state['mode'] in ('field', 'dialog', 'menu') and last_mode in ('battle', 'post_battle'):
                    # Encounter IDs are reused for random battles. Attempts and
                    # target-menu hints belong to one encounter, not one ID.
                    battle_log = dict(last=0.0, capture=0.0, probes={}, flees={}, seen={})
                last_mode = state['mode']
                atomic_json(ROOT/'live-state.json', state)
                checkpoint_saved = False
                try:
                    checkpoint_saved = bool(pad and checkpoints.tick(state, pad, stopped))
                except (OSError, ValueError) as exc:
                    # A full/unwritable save disk must not crash gameplay.
                    status['checkpoint_error'] = str(exc)
                if checkpoint_saved:
                    approved_route = None
                    continue  # Save completion must be followed by a fresh snapshot.
                if state['mode'] == 'battle' and state.get('battle'):
                    battle_id = state['battle']['id']
                    battle_log['seen'].setdefault(battle_id, time.monotonic())
                    state['flee_exhausted'] = battle_log['flees'].get(battle_id, 0) >= 2
                    state['battle_target_open'] = battle_log.get('target_open') == battle_id
                    log_battle(state, source, battle_log, args.capture)
                    tries, last_try = battle_log['probes'].get(battle_id, (0, 0.0))
                    # Probe only once a command menu can be open (not during the
                    # opening transition), retry a few times if nothing moved.
                    if (args.battle_probe and pad and tries < 4 and time.monotonic()-battle_log['seen'][battle_id] > 3
                            and time.monotonic()-last_try > 3):
                        import battle_probe
                        try:
                            report = battle_probe.probe(source, pad, stopped)
                        except (StateError, OSError):
                            pad.release()
                            continue  # Next tick reconnects/rechecks the screen.
                        found = bool(report.get('command_cursor') or report.get('target_cursor'))
                        battle_log['probes'][battle_id] = (99 if found else tries+1, time.monotonic())
                        report.update(battle_id=battle_id, attempt=tries+1)
                        battle_probe.save(report, ROOT/f'battle-menu-probe-{battle_id}-{tries+1}.json')
                        continue
                if explorer:
                    explorer.observe(state)
                if goal_watch:
                    goal_step = bool(args.story and state['mode'] == 'field' and (objective(state)[0] or {}).get('goal'))
                    handover = goal_watch.check(state, goal_step)
                    if handover:
                        pad and pad.release()
                        status.update(status='blocked', mode='goal', attention='goal', reason=handover)
                        break
                objective_reason = None
                if grind and not args.story:
                    target = None      # errands are recomputed every tick
                if args.story and state['mode'] == 'field':
                    context = (state['field_id'], state['story_progress'])
                    target, objective_reason = objective(state, skip=stale_steps.get(context, ()))
                    if target is None and objective_reason.startswith('No verified story objective'):
                        target, catalog_reason = catalog.target(state)
                        if target:
                            objective_reason = catalog_reason
                    target, objective_reason = with_exploration(explorer, state, target, objective_reason,
                                                                unsure=bool(stale_steps.get(context)))
                    target, objective_reason = with_collection(collector, state, target, objective_reason)
                if grind:
                    grind.observe(state)
                if retreat:
                    answer = retreat.answer(state)
                    if answer:
                        state['dialogue_answer'] = answer
                screen_button = screen_nudger.action(state) if screen_nudger else None
                if screen_button:
                    state['screen_nudge'] = screen_button
                    if screen_button == 'cancel' and not screen_nudger.queue and not screen_nudger.rounds:
                        with (ROOT/'decisions.jsonl').open('a', encoding='utf-8') as stream:
                            stream.write(json.dumps(dict(time=time.time(), event='screen_nudge', field_id=state['field_id'],
                                                         button='cancel', press=screen_nudger.presses))+'\n')
                if nudger:
                    nudge = nudger.action(state)
                    if nudge:
                        state['dialog_nudge'] = nudge
                        with (ROOT/'decisions.jsonl').open('a', encoding='utf-8') as stream:
                            stream.write(json.dumps(dict(time=time.time(), event='dialog_nudge', field_id=state['field_id'],
                                                         button=nudge, dialogue=state['dialogs'][0]['text'][:200]))+'\n')
                if guesser:
                    guess = guesser.answer(state)
                    if guess:
                        state['dialogue_answer'] = guess
                        if guess != last_guess:
                            last_guess = guess
                            with (ROOT/'decisions.jsonl').open('a', encoding='utf-8') as stream:
                                stream.write(json.dumps(dict(time=time.time(), event='unverified_choice', field_id=state['field_id'],
                                                             answer=guess, dialogue=state['dialogs'][0]['text'][:200]))+'\n')
                    elif state['mode'] != 'dialog':
                        last_guess = None
                # Recovery errands outrank the story route: fetch, then rest, then the grind.
                errand = None
                for source_of in (supplies, retreat, grind):
                    if source_of and state['mode'] == 'field' and errand is None:
                        found, why = source_of.target(state)
                        if why and not getattr(source_of, 'failed', None):
                            errand = target, objective_reason = found, why
                if grind and (grind.failed or retreat and retreat.failed):
                    pad and pad.release()
                    status.update(status='blocked', mode='grind', target=None,
                                  reason=f'Grind stopped: {grind.failed or retreat.failed}')
                    break
                if args.battle_assist:
                    battle_control.prepare(state)
                choices, reason = choices_for(state, target, args.story, args.battle_assist)
                if objective_reason and reason == 'Awaiting a route target for this field.':
                    reason = objective_reason
                if suspended is not None:
                    # Verified route/answer updates can unblock the same save.
                    # A movement stall still requires actual state progress.
                    # A stall is retried every RETRY_STALL seconds: the cause (a paused
                    # emulator, a pad that lost its port) is often fixed without Cloud moving.
                    retry = suspended[1].startswith(('No progress', 'Controller input')) and time.monotonic()-suspended_at >= RETRY_STALL
                    if not retry and recovery_key(state) == suspended[0] and (len(choices) == 1 or suspended[1].startswith(('No progress', 'Controller input'))):
                        pad and pad.release()
                        status.update(status='waiting', attention='dialogue' if state['mode'] == 'dialog' else 'route', reason=suspended[1], target=None)
                        atomic_json(ROOT/'laya-status.json', status)
                        time.sleep(0.1)
                        continue
                    suspended = None
                    unchanged_actions = 0
                moves = [a for a in choices if a in DIRECTION_ANGLES]
                movement = (state['mode'] == 'field' and target and len(moves) == 1
                            and not reason.startswith('Interact'))
                key = route_key(state, target, objective_reason) if movement else None
                t0 = time.monotonic()
                if movement and key == approved_route:
                    # Laya already approved this objective; movement itself
                    # never waits on inference. The executor re-validates
                    # every sample, so the pre-press guard is not needed.
                    decision = dict(action=moves[0], probability=1, probabilities={moves[0]: 1},
                                    model_confidence=None)
                    decided_by = 'executor'
                elif len(choices) > 1:
                    decision = policy.choose(state, choices, target, history)
                    decided_by = args.policy
                else:
                    # Single-action waiting is a deterministic gate, not model inference.
                    decision = dict(action='wait', probability=1, probabilities={'wait': 1}, model_confidence=None)
                    decided_by = 'state_gate'
                latency = time.monotonic()-t0
                if decided_by == 'laya':
                    timings.append(latency)
                action = decision['action']
                rejection = None
                if decision['probability'] < args.min_probability:
                    rejection = 'Selected probability below configured threshold.'
                elif STOP.exists() or time.monotonic() >= deadline:
                    rejection = 'Session stopped or duration expired.'
                elif action != 'wait' and decided_by in ('laya', 'deterministic'):
                    try:
                        rejection = guard(state, source.snapshot(attempts=1), action, args.max_state_age, target, args.story, args.battle_assist)
                    except (StateError, OSError) as exc:
                        rejection = f'State unavailable before input: {exc}'
                if rejection:
                    action = 'wait'
                stopped = lambda: STOP.exists() or time.monotonic() >= deadline
                if movement and action in DIRECTION_ANGLES:
                    approved_route = key
                if pad and movement and action in DIRECTION_ANGLES:
                    result = drive(pad, source, state, target, stopped, run=not args.walk)
                    if stalled(result):
                        # Navigation is stuck: the next movement is a decision point again.
                        approved_route = None
                        deaf_stalls = deaf_stalls+1 if result.get('pad_seen') is False else 0
                    else:
                        deaf_stalls = 0
                elif pad and state['mode'] == 'field' and target and reason.startswith('Interact') and action != 'wait':
                    # Generic interaction: release Cross + edge, face, Circle.
                    result = interact(pad, source, state, target, stopped)
                    if result.get('confirmed') and collector:
                        collector.attempted(state, target)
                    if result.get('confirmed') and supplies:
                        supplies.attempted(state, target)
                else:
                    if action == 'flee':
                        battle_log['flees'][state['battle']['id']] = battle_log['flees'].get(state['battle']['id'], 0)+1
                    if state['mode'] == 'battle' and action in ('confirm', 'cancel'):
                        # After a confirm we may be in target selection; a later
                        # cancel there is harmless at the command menu too.
                        battle_log['target_open'] = state['battle']['id'] if action == 'confirm' else None
                    # Escape needs L1+R1 held for a while, not a tap.
                    result = pad.perform(action, duration=3.0 if action=='flee' else .25 if (state.get('battle') or {}).get('menu') else .12,
                                         stopped=stopped) if pad else dict(sent=False)
                if args.battle_assist:
                    battle_control.applied(state, action, result['sent'])
                field_heal.applied(state, action, result['sent'])
                sent += int(result['sent'])
                entry = dict(time=time.time(), mode=state['mode'], field_id=state['field_id'],
                             decision=decision, applied_action=action, reason=rejection or reason,
                             source=decided_by,
                             inference_ms=round(latency*1000, 2), **result)
                with (ROOT/'decisions.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(entry)+'\n')
                    for event in collector.drain() if collector else ():
                        stream.write(json.dumps(dict(time=time.time(), **event))+'\n')
                history.append(dict(action=action, position=state['player']['position'] if state['player'] else None))
                history = history[-3:]
                progress = recovery_key(state)
                if progress != last_progress:
                    unchanged_actions = 0
                elif result['sent']:
                    unchanged_actions += 1
                last_progress = progress
                status.pop('attention', None)
                status.update(status='running', mode=state['mode'], reason=entry['reason'],
                              actions_sent=sent, last_decision=entry, pid=source.pid, emulator=state.get('emulator'),
                              objective=objective_reason, target=target,
                              battle_assist=args.battle_assist, heal_blocked=field_heal.failed,
                              supply_blocked=supplies.failed if supplies else None,
                              resting=retreat.why if retreat else None, rests=retreat.rests if retreat else 0,
                              rest_blocked=retreat.failed if retreat else None,
                              grind_battles=grind.battles if grind else None,
                              battle_strategy='advanced' if (state.get('battle') or {}).get('menu') else 'basic' if args.battle_assist else 'off',
                              dialogue=[d['text'] for d in state.get('dialogs', []) if d.get('choice_pointer')])
                atomic_json(ROOT/'laya-status.json', status)
                # A locked field is a cutscene (e.g. the blin66_5 meeting): keep
                # waiting and advancing its dialogue instead of reporting blocked.
                # Cutscenes also leave gaps between dialogue boxes where the field
                # looks free and nothing is shown, so only give up once nothing at
                # all has changed for BLOCK_GRACE seconds.
                if (state['mode'] == 'battle' and not args.battle_assist and args.execute and len(choices) == 1
                        and reason.startswith('Battle detected; experimental battle assist is not enabled')
                        and live_fight(state)):
                    # Battle OFF means the player fights; the bot waits and then
                    # clears the results screens. Say so loudly in the panel.
                    status.update(attention='battle',
                                  reason='YOUR TURN: battle in progress and Battle assist is OFF. Fight it yourself, or stop, turn Battle ON and START.')
                    atomic_json(ROOT/'laya-status.json', status)
                else:
                    status.pop('attention', None)
                if len(choices) == 1 and reason.startswith(('Navigation blocked:', 'Blocked at ')):
                    status.update(status='waiting', attention='route')
                    atomic_json(ROOT/'laya-status.json', status)
                if state['mode'] == 'dialog' and len(choices) == 1 and reason.startswith(('No verified answer', 'Expected dialogue answer')):
                    status.update(status='waiting', attention='dialogue')
                    atomic_json(ROOT/'laya-status.json', status)
                if state['mode'] == 'naming':
                    status.update(status='waiting', attention='naming', objective='Confirm the character name in FF7.')
                    atomic_json(ROOT/'laya-status.json', status)
                cutscene = state['mode'] == 'field' and (state.get('movement_locked') or state.get('fading'))
                if args.story and len(choices) == 1 and not cutscene and reason.startswith(('No verified story objective', 'No verified answer', 'Explored every story trigger')):
                    if blocked_since is None or progress != blocked_progress:
                        blocked_since, blocked_progress = time.monotonic(), progress
                    elif time.monotonic()-blocked_since >= BLOCK_GRACE:
                        if args.reconnect:
                            suspended = (recovery_key(state), reason + ' Waiting for gameplay to change or a save to load.')
                        else:
                            status.update(status='blocked', reason=reason)
                            break
                else:
                    blocked_since = None
                if deaf_stalls >= 2 and state['mode'] == 'field':
                    # Held directions never showed up in the game's own pad word.
                    deaf_stalls = 0
                    message = ('Controller input is not reaching the game: the emulator is paused, or the virtual pad is not '
                               'bound to controller port 1 (SDL-0). Retrying every 15 s.')
                    pad and pad.release()
                    if args.reconnect:
                        suspended, suspended_at = (recovery_key(state), message), time.monotonic()
                        status.update(status='waiting', attention='controller', reason=message)
                        atomic_json(ROOT/'laya-status.json', status)
                        continue
                    raise StateError(message)
                # Standing on a reached target while its step still applies is "no effect" too.
                # So is a step whose target cannot be walked to at all.
                blocked_step = objective_reason.split(':')[0][len('Blocked at '):] if (objective_reason or '').startswith('Blocked at ') else None
                idle = bool(blocked_step) or (reason.startswith(('Route target reached.', 'Navigation blocked:'))
                                              and bool(target and target.get('step')))
                reached_since = (reached_since or time.monotonic()) if idle else None
                if (unchanged_actions >= UNSURE_AFTER or reached_since and time.monotonic()-reached_since >= REACHED_GRACE) \
                        and args.story and state['mode'] == 'field' and (blocked_step or target and (target.get('step') or target.get('explore'))):
                    reached_since = None
                    target = target or dict(step=blocked_step)
                    # The objective changes nothing: stop repeating it and look around
                    # instead (every reachable trigger and model, once each).
                    if target.get('step'):
                        stale_steps.setdefault((state['field_id'], state['story_progress']), set()).add(target['step'])
                    else:
                        explorer.give_up(target['explore'])
                    with (ROOT/'decisions.jsonl').open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps(dict(time=time.time(), event='objective_no_effect', field_id=state['field_id'],
                                                     step=target.get('step'), explore=target.get('explore'),
                                                     reason=objective_reason))+'\n')
                    unchanged_actions, approved_route = 0, None
                    continue
                if unchanged_actions >= 12 and state['mode'] != 'battle':
                    # Never abort mid-battle: a released pad just lets enemies act.
                    if args.reconnect:
                        suspended = (recovery_key(state), 'No progress after 12 actions. Retrying every 15 s; move Cloud or load a save to resume sooner.')
                        suspended_at = time.monotonic()
                        pad and pad.release()
                    else:
                        raise StateError('No progress after 12 actions; stopped instead of repeating indefinitely.')
                if action == 'wait' and len(choices) == 1:
                    wait_until = tick+max(args.interval, 0.5)
                else:
                    wait_until = tick+args.interval
                while time.monotonic() < wait_until and not STOP.exists():
                    time.sleep(0.01)
        except Exception as exc:
            status.update(status='error', error=f'{type(exc).__name__}: {exc}')
            raise
        finally:
            if pad:
                pad.release()
            if source:
                source.close()
            if status['status'] not in ('error', 'blocked'):
                status['status'] = 'stopped'
            status.update(finished=time.time(), actions_sent=sent, controller_released=True,
                          model_decisions=len(timings),
                          median_inference_ms=round(statistics.median(timings)*1000, 2) if timings else None)
            atomic_json(ROOT/'laya-status.json', status)
            print(json.dumps(status), flush=True)


if __name__ == '__main__':
    main()
