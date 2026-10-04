"""Phase 1: continuous held movement, run handling, release edges, stall recovery.

All synthetic: no virtual pad is created and no window is touched.
"""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from laya_loop import Controller, RELEASE_EDGE
import laya_loop
import motion
from motion import drive, movement_command, stalled

A, B, UP, DOWN, LEFT, RIGHT = 1, 2, 4, 8, 16, 32


def field(position=(100, 100, 0)):
    return dict(mode='field', field_id=248, story_progress=264, key_items=[0]*8,
                puzzles=dict(midgar_placed=0, shinra_chests=[0, 0]),
                player=dict(position=list(position), triangle=0, collision_radius=0, facing=0),
                actors=[], dialogs=[], movement_locked=False, fading=False,
                navigation=dict(mesh=[dict(id=0, vertices=[[0, 0, 0], [1000, 0, 0], [0, 1000, 0]],
                                           neighbors=[-1, -1, -1], locked=False)],
                                direction_vectors=dict(right=[1, 0], left=[-1, 0], up=[0, 1], down=[0, -1]),
                                direction_angles=dict(right=192, left=64, up=0, down=128)))


class Clock:
    """Deterministic time: sleeping advances it; nothing really waits."""
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class RecordingPad:
    def __init__(self, clock=time.perf_counter):
        self.mask, self.reports, self.clock = 0, [], clock
    def reset(self):
        self.mask = 0
    def press_button(self, button):
        self.mask |= button
    def update(self):
        self.reports.append((self.clock(), self.mask))


def controller(clock=time.perf_counter):
    c = Controller.__new__(Controller)
    c.pad = RecordingPad(clock)
    c.buttons = dict(cancel=A, confirm=B, up=UP, down=DOWN, left=LEFT, right=RIGHT,
                     up_right=UP | RIGHT, down_right=DOWN | RIGHT)
    c.user32 = type('Window', (), {'GetForegroundWindow': lambda self: 7})()
    return c


class ScriptedSource:
    """Moves the player along `positions`; one entry per snapshot, last repeats."""
    def __init__(self, positions, changes=None):
        self.positions, self.changes, self.count = positions, changes or {}, 0
    def snapshot(self, attempts):
        assert attempts == 1
        state = field(self.positions[min(self.count, len(self.positions)-1)])
        state.update(self.changes.get(self.count, {}))
        self.count += 1
        return state


class HeldMovementTests(unittest.TestCase):
    def test_cross_stays_held_across_direction_changes(self):
        clock = Clock()
        pad = controller(clock)
        directions = iter(['right']*5 + ['up_right']*5 + ['up']*5)
        source = ScriptedSource([(100+10*i, 100, 0) for i in range(15)], {15: dict(fading=True)})
        target = dict(field_id=248, position=[900, 900, 0], radius=12)
        with patch.object(motion, 'target_action', side_effect=lambda s, t: (next(directions), 'Follow the walkmesh path.')):
            result = drive(pad, source, field(), target, lambda: False, clock=clock, sleep=clock.sleep)
        masks = [m for _, m in pad.pad.reports]
        # Every report before the final release carries Cross; direction
        # changes rewrite the report without an intermediate zero report.
        self.assertEqual(masks, [A | RIGHT, A | UP | RIGHT, A | UP, 0])
        self.assertEqual(result['direction_changes'], 2)
        self.assertEqual(result['report_writes'], 3)
        self.assertEqual(result['state_checks'], result['run_checks'])
        self.assertEqual(result['state_checks'], 15)
        self.assertEqual(result['cross_held_pct'], 100.0)
        self.assertEqual(result['macro_end'], 'Script or transition took control.')

    def test_unchanged_direction_is_not_rewritten(self):
        clock = Clock()
        pad = controller(clock)
        source = ScriptedSource([(100+5*i, 100, 0) for i in range(40)], {40: dict(mode='dialog')})
        target = dict(field_id=248, position=[900, 100, 0], radius=12)
        result = drive(pad, source, field(), target, lambda: False, clock=clock, sleep=clock.sleep)
        self.assertEqual([m for _, m in pad.pad.reports], [A | RIGHT, 0])
        self.assertEqual(result['state_checks'], 40)
        self.assertGreater(result['held_s'], 1.0)

    def test_walks_last_stretch_before_interaction(self):
        clock = Clock()
        pad = controller(clock)
        source = ScriptedSource([(100+10*i, 100, 0) for i in range(12)])
        target = dict(field_id=248, position=[200, 100, 0], radius=12, inspect=True, face=[260, 100])
        result = drive(pad, source, field(), target, lambda: False, clock=clock, sleep=clock.sleep)
        # Run until within radius+WALK_MARGIN, then the same report minus Cross.
        self.assertEqual([m for _, m in pad.pad.reports], [A | RIGHT, RIGHT, 0])
        self.assertLess(result['run_checks'], result['state_checks'])
        self.assertIn(result['macro_end'], ('Ready to inspect or interact.', 'Arrived at interaction or waypoint.'))

    def test_gateway_is_crossed_at_run(self):
        s = field()
        t = dict(field_id=248, position=[110, 100, 0], radius=0, gateway=0)
        with patch.object(motion, 'target_action', return_value=('right', 'Cross the field exit.')):
            self.assertEqual(movement_command(s, s, t)[:2], ('right', True))

    def test_walk_flag_never_holds_cross(self):
        clock = Clock()
        pad = controller(clock)
        source = ScriptedSource([(100+10*i, 100, 0) for i in range(5)], {5: dict(fading=True)})
        result = drive(pad, source, field(), dict(field_id=248, position=[900, 100, 0], radius=12),
                       lambda: False, run=False, clock=clock, sleep=clock.sleep)
        self.assertTrue(all(not m & A for _, m in pad.pad.reports))
        self.assertEqual(result['run_checks'], 0)

    def test_moving_actor_is_followed_without_release(self):
        clock = Clock()
        pad = controller(clock)
        npc = lambda x: dict(entity_name='ZAKOA', position=[x, 100, 0], triangle=0, talkable=True,
                             collision_radius=10, visible=True, solid=True)
        changes = {i: dict(actors=[npc(600+5*i)]) for i in range(10)}
        changes[10] = dict(mode='dialog')
        source = ScriptedSource([(100+10*i, 100, 0) for i in range(10)], changes)
        target = dict(field_id=248, position=[600, 100, 0], triangle=0, radius=48, actor='ZAKOA')
        result = drive(pad, source, field(), target, lambda: False, clock=clock, sleep=clock.sleep)
        self.assertEqual([m for _, m in pad.pad.reports], [A | RIGHT, 0])
        self.assertEqual(result['macro_end'], 'Gameplay mode or field changed.')

    def test_stop_releases_and_never_presses(self):
        pad = controller()
        result = drive(pad, None, field(), dict(field_id=248, position=[900, 100, 0]), lambda: True)
        self.assertFalse(result['sent'])
        self.assertEqual([m for _, m in pad.pad.reports], [0])


class StallRecoveryTests(unittest.TestCase):
    def run_stall(self, positions):
        clock = Clock()
        pad = controller(clock)
        source = ScriptedSource(positions)
        with patch.object(motion, 'target_action', return_value=('right', 'Follow the walkmesh path.')), \
             patch.object(motion, 'steering_options', return_value=([(1, 'right'), (.7, 'up_right')], [0])):
            result = drive(pad, source, field(), dict(field_id=248, position=[900, 100, 0], radius=12),
                           lambda: False, max_seconds=5, clock=clock, sleep=clock.sleep)
        return result, [m for _, m in pad.pad.reports]

    def test_stall_tries_one_alternate_then_releases(self):
        result, masks = self.run_stall([(100, 100, 0)])
        self.assertEqual(masks, [A | RIGHT, A | UP | RIGHT, A | RIGHT, 0])
        self.assertEqual(result['alternates'], 1)
        self.assertTrue(stalled(result))

    def test_alternate_that_progresses_resumes_route(self):
        # Stuck for ~0.7 s, then the sidestep frees the player.
        positions = [(100, 100, 0)]*25 + [(100+5*i, 105, 0) for i in range(200)]
        result, masks = self.run_stall(positions)
        self.assertEqual(masks[:3], [A | RIGHT, A | UP | RIGHT, A | RIGHT])
        self.assertFalse(stalled(result))
        self.assertEqual(result['macro_end'], 'Movement safety cap reached; release and replan.')


class ReleaseEdgeTests(unittest.TestCase):
    def assert_edge_before_confirm(self, reports):
        confirm = next(i for i, (t, m) in enumerate(reports) if m & B)
        offs = [i for i in range(1, confirm+1) if reports[i-1][1] & A and not reports[i][1] & A]
        self.assertTrue(offs, 'Cross was never released before confirm')
        self.assertFalse(any(m & A for t, m in reports[offs[-1]:confirm+1]))
        self.assertGreaterEqual(reports[confirm][0]-reports[offs[-1]][0], RELEASE_EDGE-0.002)

    def test_confirm_after_running_releases_cross_first(self):
        c = controller()
        c.set_movement('up', True)
        c.perform('confirm', duration=0.02)
        self.assert_edge_before_confirm(c.pad.reports)
        self.assertEqual(c.pad.reports[-1][1], 0)

    def test_macro_sequence_run_then_circle(self):
        c = controller()
        result = c.macro([('cross+up', 0.05), ('cross+right', 0.05), ('circle', 0.02)])
        masks = [m for _, m in c.pad.reports]
        self.assertEqual(masks[:2], [A | UP, A | RIGHT])
        self.assertEqual(masks[-1], 0)
        self.assert_edge_before_confirm(c.pad.reports)
        self.assertEqual(result['direction_changes'], 1)
        self.assertGreater(result['cross_held_pct'], 70)
        self.assertEqual(result['macro_end'], 'Completed.')

    def test_hold_honors_stop_and_releases(self):
        c = controller()
        calls = iter([False, True])
        result = c.hold('cross+up', 5, stopped=lambda: next(calls, True))
        self.assertEqual(c.pad.reports[-1][1], 0)
        self.assertLess(result['duration_s'], 1)
        self.assertEqual(result['macro_end'], 'STOP requested or session ended.')

    def test_unknown_button_rejected(self):
        with self.assertRaises(ValueError):
            controller().hold('triangle', 0.01)


class LoopDecisionPointTests(unittest.TestCase):
    """Laya approves a movement objective once; the executor then runs it."""
    def test_laya_consulted_once_per_objective_and_after_stall(self):
        state = field()
        state.update(pid=1, captured_monotonic=time.monotonic(), field_state=0, module_request=1,
                     party=[], gateways=[])
        target = dict(field_id=248, position=[900, 100, 0], radius=12)
        ends = iter(['Arrived at interaction or waypoint.', 'Movement stalled after alternate direction; release and replan.',
                     'Gameplay mode or field changed.', 'Gameplay mode or field changed.'])

        class Source:
            pid = 1
            def snapshot(self, attempts=100):
                return copy.deepcopy(state)
            def close(self):
                pass

        class Policy:
            calls = 0
            def choose(self, *args):
                Policy.calls += 1
                return dict(action='right', probability=.95, probabilities={'wait': .05, 'right': .95}, model_confidence=.9)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            count = [0]
            def fake_drive(*args, **kwargs):
                count[0] += 1
                if count[0] >= 4:
                    (root/'STOP').write_text('stop')
                state['player']['position'][0] += 10  # progress, so no stuck-loop error
                return dict(sent=True, foreground_unchanged=True, macro='drive', state_checks=10,
                            run_checks=10, macro_end=next(ends))
            pad = type('Pad', (), {'release': lambda self: None})()
            with patch.object(laya_loop, 'ROOT', root), patch.object(laya_loop, 'STOP', root/'STOP'), \
                 patch.object(laya_loop, 'LiveRAM', return_value=Source()), \
                 patch.object(laya_loop, 'Controller', return_value=pad), \
                 patch.object(laya_loop, 'LayaPolicy', return_value=Policy()), \
                 patch.object(laya_loop.time, 'sleep'), \
                 patch.object(laya_loop, 'objective', return_value=(target, 'Go right.')), \
                 patch.object(laya_loop, 'choices_for', return_value=({'wait': '', 'right': ''}, 'Follow the walkmesh path.')), \
                 patch.object(laya_loop, 'guard', return_value=None), \
                 patch.object(laya_loop, 'drive', side_effect=fake_drive), patch('builtins.print'):
                laya_loop.main(['--story', '--execute', '--seconds', '0'])
            entries = [json.loads(l) for l in (root/'decisions.jsonl').read_text().splitlines()]
        self.assertEqual([e['source'] for e in entries], ['laya', 'executor', 'laya', 'executor'])
        self.assertEqual(Policy.calls, 2)
        self.assertEqual(entries[1]['inference_ms'], 0)


if __name__ == '__main__':
    unittest.main()
