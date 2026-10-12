"""Phase 3: data-driven route table, verification flags, resync, blocked-on-unknown."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import route
from route import RouteError, load, current_step, build_target, choice_answer
from story import objective, target_action
from collect import Collector, with_collection
from test_phase2 import grid_state, chest

STEPS, CHOICES = load()


def state(field, progress, **bytes_):
    """bytes_: b<bank>_<address>=value, e.g. b1_68=1, b5_21=1."""
    banks = {str(b): bytearray(256) for b in (1, 3, 5, 11, 13, 15)}
    banks['1'][0:2] = progress.to_bytes(2, 'little')
    for key, value in bytes_.items():
        bank, address = key[1:].split('_')
        banks[bank][int(address)] = value
    return dict(mode='field', field_id=field, story_progress=progress,
                banks={k: v.hex() for k, v in banks.items()}, key_items=list(banks['1'][64:72]))


def step_id(field, progress, **bytes_):
    step = current_step(state(field, progress, **bytes_), STEPS)
    return step and step['id']


K62, K65 = dict(b1_67=0x80), dict(b1_68=1)


class TableTests(unittest.TestCase):
    def test_every_step_has_a_verification_flag_or_leaves_the_field(self):
        for step in STEPS:
            with self.subTest(step=step['id']):
                self.assertTrue(step['until'] or step['target']['type'] in route.EXIT_TYPES+('puzzle',))
                self.assertIn(step['source'], ('live', 'script'))

    def test_loader_rejects_unverifiable_step(self):
        bad = dict(steps=[dict(id='x', field=1, progress=[0, 1], target=dict(type='actor', entity=1), label='', source='script')])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'bad.json'
            path.write_text(json.dumps(bad))
            with self.assertRaises(RouteError):
                load([path])

    def test_loader_rejects_unknown_target_type(self):
        bad = dict(steps=[dict(id='x', field=1, progress=[0, 1], until=[{'progress_at_least': 2}],
                               target=dict(type='teleport'), label='', source='script')])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'bad.json'
            path.write_text(json.dumps(bad))
            with self.assertRaises(RouteError):
                load([path])


class MigratedRouteTests(unittest.TestCase):
    """Same decisions as the old if-chain for floors 61-65."""
    def test_floors_61_to_65(self):
        self.assertEqual(step_id(242, 264), 'f62-back-to-61')
        self.assertEqual(step_id(241, 264), 'f61-keycard62')
        self.assertEqual(step_id(241, 264, **K62), 'f61-to-62')
        self.assertEqual(step_id(242, 264, **K62), 'f62-domino')
        self.assertEqual(step_id(242, 264, **K62, **K65), 'f62-to-63')
        self.assertEqual(step_id(245, 264, **K62, **K65), 'f63-to-64')
        self.assertEqual(step_id(247, 264, **K62, **K65), 'f64-to-65')
        self.assertEqual(step_id(248, 264, **K62, **K65), 'f65-midgar-model')
        self.assertEqual(step_id(249, 264, **K62, **K65), 'f65-midgar-model')

    def test_verification_flag_resyncs_after_reload(self):
        # Keycard 62 already in the save: the floor 61 talk step is skipped.
        self.assertNotEqual(step_id(241, 264, **K62), 'f61-keycard62')

    def test_dialogue_answers_from_table(self):
        s = state(249, 264)
        self.assertIsNone(choice_answer(s, CHOICES, 'Place the Midgar parts\nDo nothing'))
        s = state(249, 264, b1_68=0x81)
        self.assertEqual(choice_answer(s, CHOICES, 'Place the Midgar parts\nDo nothing'), 'Place the Midgar parts')
        s = state(242, 264); s['puzzles'] = dict(domino_password='BOMB')
        self.assertEqual(choice_answer(s, CHOICES, "Password?\nBEST\nBOMB\nMAKO"), 'BOMB')
        self.assertIsNone(choice_answer(state(250, 270), CHOICES, 'Buy?\nYes\nNo'))


class ShinraUpperFloorTests(unittest.TestCase):
    def test_floor67_lower_level_uses_verified_elevator(self):
        from navigation import NoPath, steering
        s = json.loads((Path(__file__).parent/'fixtures/live-state-256-unreachable.json').read_text())
        step = current_step(s, STEPS)
        primary = dict(step, target={k: v for k, v in step['target'].items() if k != 'fallback'})
        with self.assertRaises(NoPath):
            steering(s, build_target(s, primary))
        target, _ = objective(s)
        self.assertEqual(target['line'], [[-475, 891, 0], [-396, 1061, 0]])
        self.assertIsNotNone(steering(s, target)[0])

    def test_floor_66_eavesdrop_sequence(self):
        self.assertEqual(step_id(250, 264), 'f66-restroom')
        self.assertEqual(step_id(252, 264), 'f66-vent')
        self.assertEqual(step_id(253, 266), 'f66-duct')
        self.assertEqual(step_id(252, 269), 'f66-restroom-out')
        self.assertEqual(step_id(250, 269), 'f66-follow-hojo')
        self.assertEqual(step_id(250, 270), 'f66-follow-hojo-2')
        self.assertEqual(step_id(250, 271), 'f66-stairs-67')

    def test_floors_67_68(self):
        self.assertEqual(step_id(256, 272), 'f67-follow-hojo')
        self.assertEqual(step_id(259, 273), 'f67-jenova')
        self.assertIsNone(step_id(256, 275))        # ambiguous exits: blocked, not guessed
        self.assertEqual(step_id(256, 278), 'f67-after-jenova')
        self.assertEqual(step_id(256, 281), 'f67-to-68')
        self.assertEqual(step_id(262, 282), 'f68-principal-lab')
        self.assertEqual(step_id(263, 284), 'f68-back-to-lab')
        self.assertEqual(step_id(262, 284), 'f68-keycard68')
        self.assertEqual(step_id(262, 285, b1_226=0x20), 'f68-elevator')

    def test_jail_state_machine(self):
        self.assertEqual(step_id(258, 293), 'jail-cell-door')
        self.assertEqual(step_id(258, 296, b1_232=0x10), 'jail-step-out')
        self.assertEqual(step_id(258, 296), 'jail-dead-guard')
        self.assertEqual(step_id(258, 296, b5_21=1), 'jail-wake-tifa')
        self.assertEqual(step_id(258, 296, b5_21=1, b5_16=1), 'jail-tifa-again')
        self.assertEqual(step_id(258, 296, b5_21=1, b5_16=2), 'jail-barret')
        self.assertEqual(step_id(258, 296, b5_21=1, b5_16=2, b5_20=1), 'jail-blood-trail')
        # Party formation (a menu) is not verified: stop there.
        self.assertIsNone(step_id(258, 296, b5_21=1, b5_16=2, b5_20=2))

    def test_unknown_field_blocks_with_reason(self):
        s = state(700, 999)
        target, reason = objective(s)
        self.assertIsNone(target)
        self.assertTrue(reason.startswith('No verified story objective for field 700'))


class TargetBuildTests(unittest.TestCase):
    def setUp(self):
        _, actor = chest(6, 'sikabane', 500, 500)
        self.s = grid_state([], [actor], player=(40, 40))
        self.s.update(field_id=258, story_progress=296,
                      gateways=[dict(id=0, destination=250, line=[[990, 400, 0], [990, 600, 0]]),
                                dict(id=3, destination=259, line=[[400, 990, 0], [600, 990, 0]])])

    def test_line_trigger_is_crossed_like_an_exit(self):
        target = build_target(self.s, dict(target=dict(type='line', line=[[300, 500, 0], [700, 500, 0]])))
        self.assertEqual((target['gateway'], target['position']), (-1, [500, 500, 0]))
        near = copy.deepcopy(self.s); near['player']['position'] = [500, 490, 0]
        from navigation import locate
        near['player']['triangle'] = locate(near['navigation']['mesh'], [500, 490, 0])
        self.assertEqual(target_action(near, target), ('up', 'Cross the field exit.'))
        self.assertIs(with_collection(Collector(), self.s, target, 'x')[0], target)   # no chest detour at a scene line

    def test_gateway_id_zero_still_gets_collection_detour(self):
        c = Collector()
        treasure, actor = chest(9, 'box0', 200, 200)
        s = grid_state([treasure], [actor], player=(40, 40))
        target = dict(field_id=s['field_id'], position=[990, 500, 0], radius=0, gateway=0)
        self.assertEqual(with_collection(c, s, target, 'Leave.')[0]['actor'], 'box0')

    def test_gateway_by_position_needs_a_nearby_exit(self):
        self.assertEqual(build_target(self.s, dict(target=dict(type='gateway_at', position=[980, 510, 0])))['gateway'], 0)
        from navigation import NoPath
        with self.assertRaises(NoPath):
            build_target(self.s, dict(target=dict(type='gateway_at', position=[100, 100, 0])))

    def test_reachable_primary_exit_kept_when_fallback_exists(self):
        spec = dict(type='gateway_at', position=[980, 510, 0],
                    fallback=dict(type='line', line=[[300, 500, 0], [700, 500, 0]]))
        self.assertEqual(build_target(self.s, dict(target=spec))['gateway'], 0)

    def test_actor_by_entity_and_ok_line_and_hold(self):
        target = build_target(self.s, dict(target=dict(type='actor', entity=6)))
        self.assertEqual((target['actor'], target['entity']), ('sikabane', 6))
        ok = build_target(self.s, dict(target=dict(type='ok_line', line=[[300, 420, 0], [700, 420, 0]])))
        self.assertNotEqual(target_action(self.s, ok)[0], 'confirm')
        touching = copy.deepcopy(self.s); touching['player']['position'] = [450, 425, 0]
        self.assertEqual(target_action(touching, ok)[0], 'confirm')
        hold = build_target(self.s, dict(target=dict(type='hold', direction='down')))
        self.assertEqual(target_action(self.s, hold)[0], 'down')


if __name__ == '__main__':
    unittest.main()


class PinchRegressionTests(unittest.TestCase):
    def test_floor_61_wall_pinch_still_advances(self):
        path = Path(__file__).parent/'fixtures/live-state-241-blocked.json'
        if not path.exists():
            self.skipTest('fixture unavailable')
        s = json.loads(path.read_text())
        target, _ = objective(s)
        direction, reason = target_action(s, target)
        self.assertIn(direction, ('left', 'down_left'))   # both advance; neither walks into an NPC
