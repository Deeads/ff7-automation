import time
import unittest

from battle_strategy import PHOENIX_DOWN
from supplies import Supplies, wanted
from test_phase2 import grid_state

INDEX = {'here': dict(exits=[11, 12], pickups=[]),
         'hall': dict(exits=[10, 13], pickups=[]),
         'dead_end': dict(exits=[10], pickups=[dict(name='BOX', flag=[1, 5, 0], items=[0])]),
         'lockers': dict(exits=[11], pickups=[dict(name='RLINA', flag=[3, 172, 0], items=[PHOENIX_DOWN])])}
NAMES = {10: 'here', 11: 'hall', 12: 'dead_end', 13: 'lockers'}
LOCKER = dict(entity=3, name='RLINA', flag=[3, 172, 0], taken=False, guarded=True,
              grants=[dict(kind='item', id=PHOENIX_DOWN, amount=1, literal=True)])


class Clock:
    now = 50.0
    def __call__(self): return self.now


def state(field=10, name='here', exits=(11, 12), treasures=(), actors=(), ko=True, stock=0, taken=False):
    s = grid_state(list(treasures), list(actors))
    s.update(field_id=field, field_name=name, movement_locked=False, fading=False,
             party=[dict(name='Cloud', hp=0 if ko else 400, max_hp=688), dict(name='Tifa', hp=500, max_hp=609)],
             items={PHOENIX_DOWN: stock} if stock else {},
             banks={'1': bytes(256).hex(), '3': bytes([taken if i == 172 else 0 for i in range(256)]).hex()},
             gateways=[dict(id=i, destination=d, line=[[100+300*i, 0, 0], [200+300*i, 0, 0]],
                            destination_position=[0, 0, 0]) for i, d in enumerate(exits)])
    return s


def actor(entity=3, position=(850, 850, 0)):
    return dict(entity=entity, entity_name='RLINA', position=list(position), triangle=0, talkable=True,
                visible=True, solid=True, collision_radius=20, distance=1.0)


class SupplyTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.supplies = Supplies('.', index=INDEX, names=dict(NAMES), clock=self.clock)

    def test_wanted_only_for_a_ko_member_without_stock(self):
        self.assertEqual(wanted(state()), PHOENIX_DOWN)
        self.assertIsNone(wanted(state(ko=False)))
        self.assertIsNone(wanted(state(stock=1)))
        self.assertIsNone(wanted(dict(party=[dict(hp=0, max_hp=5)], items={})))     # game over, not a search

    def test_routes_through_exits_to_the_untaken_pickup(self):
        target, reason = self.supplies.target(state())
        self.assertEqual((target['gateway'], target['supply']), (0, 13))
        self.assertIn('lockers (2 fields away)', reason)
        self.assertTrue(self.supplies.searching(state()))

    def test_taken_pickup_is_not_a_source(self):
        target, reason = self.supplies.target(state(taken=True))
        self.assertIsNone(target)
        self.assertIn('No untaken Phoenix Down', self.supplies.failed)
        self.assertFalse(self.supplies.searching(state(taken=True)))

    def test_opens_the_pickup_in_the_current_field(self):
        target, reason = self.supplies.target(state(13, 'lockers', (11,), [LOCKER], [actor()]))
        self.assertEqual((target['entity'], target['collect']['name']), (3, 'RLINA'))
        self.assertIn('open RLINA', reason)

    def test_unopenable_pickup_is_given_up_after_settling_without_ping_pong(self):
        there = state(13, 'lockers', (11,), [LOCKER], [])            # locker actor never appears
        self.assertEqual(self.supplies.target(there), (None, 'Fetch a Phoenix Down: waiting for the pickup to become reachable.'))
        self.assertIsNone(self.supplies.failed)
        self.clock.now += 5
        self.assertIsNone(self.supplies.target(there)[0])
        self.assertIn('No untaken', self.supplies.failed)

    def test_two_failed_opens_exhaust_the_pickup(self):
        there = state(13, 'lockers', (11,), [LOCKER], [actor()])
        for _ in range(2):
            target, _ = self.supplies.target(there)
            self.supplies.attempted(there, target)
        self.clock.now += 5
        self.assertIsNone(self.supplies.target(there)[0])
        self.assertIsNotNone(self.supplies.failed)

    def test_no_involvement_when_nothing_is_needed_or_field_is_busy(self):
        self.assertEqual(self.supplies.target(state(ko=False)), (None, None))
        self.assertEqual(self.supplies.target(dict(state(), movement_locked=True)), (None, None))
        self.assertEqual(self.supplies.target(dict(state(), mode='dialog')), (None, None))

    def test_search_state_clears_once_the_item_is_in_stock(self):
        self.supplies.target(state(taken=True))
        self.assertIsNotNone(self.supplies.failed)
        self.supplies.started = self.clock.now
        self.assertEqual(self.supplies.target(state(stock=1)), (None, None))
        self.assertIsNone(self.supplies.failed)

    def test_time_budget(self):
        self.supplies.target(state())
        self.clock.now += 601
        self.assertIsNone(self.supplies.target(state())[0])
        self.assertIn('out of time', self.supplies.failed)


if __name__ == '__main__':
    unittest.main()
