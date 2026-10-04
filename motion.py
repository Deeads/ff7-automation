"""Deterministic, feedback-controlled field movement using only the virtual gamepad.

Once a route target is approved, `drive` holds one combined pad report
(direction + optional Cross/run) and rewrites it only when the wanted
direction or run flag changes. It never releases between samples; the pad is
released only when the executor ends (arrival, interaction, mode/field change,
script lock/fade, unrecoverable stall, STOP, error).
"""
import math
import time

from ff7_state import DIRECTION_ANGLES, StateError
from navigation import NoPath, steering_options, segment_distance
from story import target_action, hold_direction

SAMPLE_SECONDS = 0.03
STALL_SECONDS = 0.6
ALTERNATE_SECONDS = 0.3
MAX_ALTERNATES = 3
# Walk (no Cross) for the last stretch before an interaction point so the
# run speed cannot overshoot a trigger triangle or talk radius.
WALK_MARGIN = 30
STALL_ENDS = ('Movement stalled',)


def movement_command(initial, current, target, run=True):
    if current['mode'] != 'field' or current['field_id'] != initial['field_id']:
        return None, False, 'Gameplay mode or field changed.'
    if current.get('movement_locked') or current.get('fading'):
        return None, False, 'Script or transition took control.'
    for key in ('key_items', 'puzzles', 'story_progress'):
        if initial.get(key) != current.get(key):
            return None, False, 'Story or puzzle state changed.'
    if target.get('actor'):
        actor = next((a for a in current.get('actors',[]) if a['entity_name']==target['actor']),None)
        if not actor or math.dist(actor['position'],target['position'])>12:
            return None, False, 'Interaction target moved; replan.'
    if target.get('hold'):
        return hold_direction(current, target), False, 'Advance along the ladder or duct.'
    if target.get('ok_line'):
        distance = segment_distance(current['player']['position'], *target['line'])
    else:
        distance = math.dist(current['player']['position'][:2],target['position'][:2])
    on_trigger = 'triangle' not in target or current['player']['triangle']==target['triangle'] or not target.get('inspect')
    if distance <= target.get('radius',12) and target.get('gateway') is None and on_trigger:
        return None, False, 'Arrived at interaction or waypoint.'
    action, reason = target_action(current,target)
    if action not in DIRECTION_ANGLES or reason.startswith('Interact'):
        return None, False, 'Ready to inspect or interact.'
    # Run is Cross (Xbox A), the same game button as keyboard X / Macro 2.
    # Gateways are crossed at full run; anything else walks the last stretch.
    running = run and (target.get('gateway') is not None or distance > target.get('radius',12)+WALK_MARGIN)
    return action, running, reason


def track_actor(current, target):
    """Follow a wandering NPC instead of releasing the pad every time it steps."""
    if not target.get('actor'):
        return target
    matches = [a for a in current.get('actors',[]) if a['entity_name']==target['actor']]
    if not matches:
        return target
    actor = min(matches, key=lambda a: math.dist(a['position'],target['position']))
    return dict(target, position=actor['position'], triangle=actor['triangle'])


def alternate_direction(current, target, primary):
    try:
        ranked, _ = steering_options(current, target)
    except NoPath:
        return None
    return next((name for _, name in ranked or () if name != primary), None)


def drive(controller, source, initial, target, stopped, run=True, max_seconds=20.0,
          clock=time.perf_counter, sleep=time.sleep):
    foreground = controller.user32.GetForegroundWindow()
    started = clock()
    samples = running_samples = direction_changes = report_writes = alternates = 0
    held = None               # (direction, running) currently in the pad report
    held_seconds = cross_seconds = 0.0
    last_tick = started
    last_position = initial['player']['position']
    progressed_at = started
    alternate = None          # (direction, hold_until)
    sidestep_at = None
    reason = 'Executor ended.'
    try:
        while True:
            now = clock()
            if held:
                held_seconds += now-last_tick
                cross_seconds += (now-last_tick) if held[1] else 0
            last_tick = now
            if stopped():
                reason = 'STOP requested or session ended.'
                break
            if now-started >= max_seconds:
                reason = 'Movement safety cap reached; release and replan.'
                break
            # Never spend the usual multi-second load retry with input held.
            try:
                current = source.snapshot(attempts=1)
                target = track_actor(current, target)
                action, running, reason = movement_command(initial,current,target,run)
            except (StateError, NoPath) as exc:
                reason = f'State or path changed; release and replan ({exc}).'
                break
            if action is None:
                break
            position = current['player']['position']
            if math.dist(position,last_position) > 1:
                last_position = position
                progressed_at = now
            elif now-progressed_at > STALL_SECONDS:
                # One sidestep per stall. No progress since the last sidestep
                # (or too many sidesteps) means the planner must replan.
                if progressed_at == sidestep_at or alternates >= MAX_ALTERNATES:
                    reason = 'Movement stalled after alternate direction; release and replan.'
                    break
                choice = alternate_direction(current, target, action)
                if choice is None:
                    reason = 'Movement stalled with no alternate direction; release and replan.'
                    break
                alternate = (choice, now+ALTERNATE_SECONDS)
                alternates += 1
                progressed_at = sidestep_at = now
            if alternate:
                if now >= alternate[1]:
                    alternate = None
                else:
                    action = alternate[0]
            wanted = (action, running)
            if wanted != held:
                if held and held[0] != action:
                    direction_changes += 1
                # One report carries direction + Cross: no release/re-press.
                controller.set_movement(action, running)
                report_writes += 1
                held = wanted
            samples += 1
            running_samples += int(running)
            until = now+SAMPLE_SECONDS
            while clock() < until and not stopped():
                sleep(0.005)
    finally:
        controller.release()
    duration = clock()-started
    return dict(sent=report_writes > 0,
                foreground_unchanged=foreground==controller.user32.GetForegroundWindow(),
                macro='drive', state_checks=samples, run_checks=running_samples,
                duration_s=round(duration,3), held_s=round(held_seconds,3),
                cross_held_pct=round(100*cross_seconds/held_seconds,1) if held_seconds else 0.0,
                direction_changes=direction_changes, report_writes=report_writes,
                intermediate_releases=0, alternates=alternates, macro_end=reason)


def stalled(result):
    return result.get('macro_end','').startswith(STALL_ENDS)


# Older name kept for callers/tests; the executor has no short duration cap.
def follow_route(controller, source, initial, target, stopped, run=True, duration=20.0):
    return drive(controller, source, initial, target, stopped, run, max_seconds=duration)


FACE_SECONDS = 0.05
FACE_TRIES = 3


def interact(controller, source, state, target, stopped):
    """Approach is done by `drive` (walking the last stretch). Here: make sure
    Cross is released (one edge), face the target with short taps, then Circle
    once. The resulting dialogue is left to the dialogue decision logic."""
    foreground = controller.user32.GetForegroundWindow()
    controller.release_edge(stopped)
    turns = 0
    reason = 'Could not face the interaction target.'
    current = state
    for attempt in range(FACE_TRIES+1):
        if stopped():
            reason = 'STOP requested or session ended.'
            break
        if attempt:
            try:
                current = source.snapshot(attempts=1)
            except StateError as exc:
                reason = f'State changed before interaction ({exc}).'
                break
        if current['mode'] != 'field' or current.get('movement_locked') or current['field_id'] != target['field_id']:
            reason = 'Game left free field control before interaction.'
            break
        try:
            action, why = target_action(current, track_actor(current, target))
        except NoPath as exc:
            reason = f'Target no longer reachable ({exc}).'
            break
        if action == 'confirm':
            result = controller.perform('confirm', stopped=stopped)
            return dict(result, macro='interact', turns=turns, confirmed=result['sent'],
                        foreground_unchanged=result['foreground_unchanged'] and
                        foreground == controller.user32.GetForegroundWindow(),
                        macro_end='Pressed Circle on the target.' if result['sent'] else 'STOP before Circle.')
        if action not in DIRECTION_ANGLES or not why.startswith('Interact'):
            reason = 'Target is out of interaction range; replan the approach.'
            break
        if attempt == FACE_TRIES:
            break
        controller.hold(action, FACE_SECONDS, stopped)
        turns += 1
    controller.release()
    return dict(sent=turns > 0, foreground_unchanged=foreground == controller.user32.GetForegroundWindow(),
                macro='interact', turns=turns, confirmed=False, macro_end=reason)
