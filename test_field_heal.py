import copy
from pathlib import Path
import time
import unittest

from battle_strategy import POTION, HI_POTION, PHOENIX_DOWN, RESTORE_MATERIA
from field_heal import FieldHeal, Step, plan, TOP_UP
from field_menu import next_input, commits, read_menu
from ff7_state import decode
from laya_loop import choices_for, guard, recovery_key


def party(cloud=(600, 688, 100), tifa=(500, 609, 96), red=(600, 717, 100)):
    return [dict(name=name, hp=hp, max_hp=top, mp=mp, max_mp=150, materia=materia)
            for name, (hp, top, mp), materia in (('Cloud', cloud, [52]), ('Tifa', tifa, [RESTORE_MATERIA]),
                                                 ('Red XIII', red, [49]))]


def state(mode='field', menu=None, **members):
    return dict(pid=7, mode=mode, field_id=250, field_state=0, module_request=1, story_progress=284,
                party=party(**members), dialogs=[], player={'position': [0, 0, 0]}, movement_locked=False,
                fading=False, items={POTION: 1, HI_POTION: 2, PHOENIX_DOWN: 1},
                item_slots={POTION: 0, PHOENIX_DOWN: 3, HI_POTION: 11}, party_slots=[0, 1, 2],
                menu_access=dict(visible=0xFEFF, locked=0), menu=menu,
                navigation={'direction_vectors': {}}, captured_monotonic=time.monotonic())


MAIN = dict(screen='main', level=0, cursor=0, member=0)


class PlanTests(unittest.TestCase):
    def test_healthy_party_needs_nothing(self):
        self.assertIsNone(plan(party(), {POTION: 5, PHOENIX_DOWN: 5}, 0.5))

    def test_ko_healer_is_revived_before_other_ko(self):
        step = plan(party(cloud=(0, 688, 100), tifa=(0, 609, 96), red=(182, 717, 125)), {PHOENIX_DOWN: 1}, 0.5)
        self.assertEqual((step.kind, step.item, step.target), ('item', PHOENIX_DOWN, 1))

    def test_ko_without_phoenix_down_is_left_for_the_stop_rule(self):
        self.assertIsNone(plan(party(cloud=(0, 688, 100)), {POTION: 3}, 0.5))

    def test_cure_targets_lowest_ratio_and_keeps_one_cast(self):
        hurt = party(cloud=(300, 688, 100), red=(182, 717, 125))
        step = plan(hurt, {POTION: 1}, 0.5)
        self.assertEqual((step.kind, step.caster, step.target), ('magic', 1, 2))
        hurt[1]['mp'] = 9          # one Cure left: keep it for battle, use the item
        self.assertEqual(plan(hurt, {POTION: 1}, 0.5).item, POTION)
        self.assertIsNone(plan(hurt, {}, 0.5))

    def test_ko_caster_cannot_cast_and_potion_size_follows_missing_hp(self):
        hurt = party(tifa=(0, 609, 96), red=(182, 717, 125))
        self.assertEqual(plan(hurt, {POTION: 1, HI_POTION: 1}, 0.5).item, HI_POTION)
        hurt[2].update(hp=100, max_hp=220)
        self.assertEqual(plan(hurt, {POTION: 1, HI_POTION: 1}, 0.5).item, POTION)

    def test_all_ko_is_game_over_not_a_plan(self):
        self.assertIsNone(plan(party(cloud=(0, 688, 0), tifa=(0, 609, 0), red=(0, 717, 0)), {PHOENIX_DOWN: 3}, 0.5))


class NavigationTests(unittest.TestCase):
    item = Step('item', 1, item=PHOENIX_DOWN, reason='Revive Tifa.')
    cure = Step('magic', 2, caster=1, reason='Tifa casts Cure on Red XIII.')

    def walk(self, step, menus, slot=None):
        return [next_input(menu, step, slot)[0] for menu in menus]

    def test_item_path(self):
        self.assertEqual(self.walk(self.item, [
            None, dict(MAIN, cursor=1), MAIN,
            dict(screen='item', phase='top', column=1, slot=0, target=0),
            dict(screen='item', phase='top', column=0, slot=0, target=0),
            dict(screen='item', phase='list', column=0, slot=0, target=0),
            dict(screen='item', phase='list', column=0, slot=12, target=0),
            dict(screen='item', phase='list', column=0, slot=3, target=0),
            dict(screen='item', phase='target', column=0, slot=3, target=0),
            dict(screen='item', phase='target', column=0, slot=3, target=1)], slot=3),
            [None, 'up', 'confirm', 'left', 'confirm', 'down', 'up', 'confirm', 'down', 'confirm'])

    def test_wrong_item_or_screen_is_cancelled_never_confirmed(self):
        self.assertEqual(self.walk(self.item, [
            dict(screen='item', phase='target', column=0, slot=4, target=1),
            dict(screen='magic', phase='target', member=1, row=0, cell=0, target=1),
            dict(screen='other'), dict(MAIN, level=1), dict(MAIN, level=2)], slot=3), ['cancel']*5)
        self.assertEqual(next_input(dict(screen='item', phase='target', column=0, slot=3, target=1), self.item)[0], 'cancel')

    def test_magic_path(self):
        magic = dict(screen='magic', member=1, row=0, cell=0, target=0)
        self.assertEqual(self.walk(self.cure, [
            MAIN, dict(MAIN, cursor=1), dict(MAIN, cursor=1, level=1), dict(MAIN, cursor=1, level=1, member=1),
            dict(magic, phase='top', row=2), dict(magic, phase='top'),
            dict(magic, phase='list', cell=4), dict(magic, phase='list', cell=1), dict(magic, phase='list'),
            dict(magic, phase='target'), dict(magic, phase='target', target=2)]),
            ['down', 'confirm', 'down', 'confirm', 'up', 'confirm', 'up', 'left', 'confirm', 'down', 'confirm'])

    def test_other_casters_magic_screen_is_left(self):
        self.assertEqual(next_input(dict(screen='magic', phase='target', member=0, row=0, cell=0, target=2), self.cure)[0], 'cancel')

    def test_only_target_confirmation_commits(self):
        self.assertTrue(commits(dict(screen='item', phase='target'), 'confirm'))
        self.assertFalse(commits(dict(screen='item', phase='list'), 'confirm'))
        self.assertFalse(commits(dict(screen='item', phase='target'), 'down'))
        self.assertFalse(commits(MAIN, 'confirm'))


class Clock:
    now = 100.0
    def __call__(self): return self.now


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.heal = FieldHeal(0.5, self.clock)

    def tick(self, s, send=True):
        active = self.heal.prepare(s)
        action = s['heal']['input'] if active else None
        if action and send:
            self.heal.applied(s, action, True)
            self.clock.now += 1
        return action if active else False

    def test_off_and_healthy_do_nothing(self):
        self.assertFalse(FieldHeal(0).prepare(state(red=(1, 717, 0))))
        s = state()
        self.assertFalse(self.heal.prepare(s))
        self.assertNotIn('heal', s)

    def test_opens_menu_only_on_a_free_field_and_gives_up_if_it_stays_shut(self):
        self.assertFalse(self.heal.prepare(dict(state(red=(100, 717, 100)), movement_locked=True)))
        self.assertFalse(self.heal.prepare(dict(state(red=(100, 717, 100)), mode='dialog')))
        for _ in range(3):
            self.assertEqual(self.tick(state(red=(100, 717, 100))), 'switch')
        self.assertFalse(self.tick(state(red=(100, 717, 100))))
        self.assertIn('did not open', self.heal.failed)
        # A new field (or a battle) makes the failure stale.
        self.assertEqual(self.tick(dict(state(red=(100, 717, 100)), field_id=251)), 'switch')

    def test_input_waits_for_the_menu_to_settle(self):
        s = state(red=(100, 717, 100))
        self.heal.prepare(s); self.heal.applied(s, 'switch', True)
        s = state('menu', MAIN, red=(100, 717, 100))
        self.assertTrue(self.heal.prepare(s))
        self.assertIsNone(s['heal']['input'])
        self.clock.now += 1
        self.heal.prepare(s)
        self.assertEqual(s['heal']['input'], 'down')

    def test_commit_waits_for_hp_then_tops_up_and_releases_the_menu(self):
        target = dict(screen='magic', phase='target', member=1, row=0, cell=0, target=2)
        self.assertEqual(self.tick(state('menu', target, red=(100, 717, 100))), 'confirm')
        s = state('menu', target, red=(100, 717, 100))
        self.clock.now = 100.5
        self.assertTrue(self.heal.prepare(s))               # outcome not visible yet: no second cast
        self.assertIsNone(s['heal']['input'])
        self.assertEqual(self.tick(state('menu', target, red=(490, 717, 100))), 'confirm')   # 68% < top-up buffer
        self.assertLess(490/717, TOP_UP)
        self.assertFalse(self.tick(state('menu', target, red=(717, 717, 100))))             # default Cross closes it
        self.assertIsNone(self.heal.failed)

    def test_two_confirmations_without_effect_stop_recovery(self):
        target = dict(screen='item', phase='target', column=0, slot=11, target=2)
        hurt = lambda: state('menu', target, tifa=(500, 609, 0), red=(100, 717, 100))
        self.assertEqual(self.tick(hurt()), 'confirm')
        self.clock.now += 2
        self.assertEqual(self.tick(hurt()), 'confirm')
        self.clock.now += 2
        self.assertFalse(self.tick(hurt()))
        self.assertIn('without any HP change', self.heal.failed)

    def test_hidden_or_locked_menu_entries_are_not_used(self):
        s = state(red=(100, 717, 100))
        s['menu_access'] = dict(visible=0xFEFF & ~2, locked=1)      # no Magic, Item locked
        self.assertFalse(self.heal.prepare(s))
        s['menu_access'] = dict(visible=0xFEFF & ~2, locked=0)
        self.heal.prepare(s)
        self.assertEqual(s['heal']['step']['item'], HI_POTION)

    def test_party_rows_follow_formation_slots(self):
        s = state('menu', dict(screen='item', phase='target', column=0, slot=11, target=1), tifa=(100, 609, 0))
        s['party'] = s['party'][1:]; s['party_slots'] = [1, 2]       # front slot empty
        self.heal.prepare(s)
        self.assertEqual((s['heal']['step']['target'], s['heal']['input']), (0, 'confirm'))


class LoopTests(unittest.TestCase):
    def test_choices_follow_recovery_and_default_menu_close_remains(self):
        s = state('menu', MAIN, red=(100, 717, 100))
        self.assertEqual(set(choices_for(s)[0]), {'wait', 'cancel'})
        FieldHeal(0.5, Clock()).prepare(s)
        self.assertEqual(set(choices_for(s)[0]), {'wait', 'down'})
        s = state(red=(100, 717, 100))
        FieldHeal(0.5, Clock()).prepare(s)
        self.assertEqual(set(choices_for(s, story=True)[0]), {'wait', 'switch'})

    def test_guard_rejects_a_moved_cursor_and_keeps_the_plan(self):
        s = state('menu', MAIN, red=(100, 717, 100))
        FieldHeal(0.5, Clock()).prepare(s)
        fresh = copy.deepcopy(s); del fresh['heal']
        self.assertIsNone(guard(s, fresh, 'down', 2))
        fresh['menu'] = dict(MAIN, cursor=1)
        self.assertEqual(guard(s, fresh, 'down', 2), 'Game state changed while Laya was deciding.')

    def test_menu_movement_counts_as_progress(self):
        self.assertNotEqual(recovery_key(state('menu', MAIN)), recovery_key(state('menu', dict(MAIN, cursor=1))))


class OwnedCaptureTests(unittest.TestCase):
    def menu(self, name):
        path = Path(__file__).parent/'fixtures'/name
        if not path.exists():
            self.skipTest('Owned-game capture not present.')
        return decode(path.read_bytes())

    def test_live_menu_positions(self):
        self.assertIsNone(self.menu('ram-menu-opening.bin')['menu'])
        self.assertEqual(self.menu('ram-menu-main.bin')['menu'], MAIN)
        self.assertEqual(self.menu('ram-menu-magic-member.bin')['menu'], dict(MAIN, level=1, cursor=1, member=1))
        s = self.menu('ram-menu-item-target.bin')
        self.assertEqual(s['menu'], dict(screen='item', phase='target', column=0, slot=3, target=1))
        self.assertEqual((s['item_slots'][PHOENIX_DOWN], s['party_slots']), (3, [0, 1, 2]))
        self.assertEqual(self.menu('ram-menu-magic-grid.bin')['menu']['cell'], 1)
        self.assertEqual(self.menu('ram-menu-magic-target.bin')['menu'],
                         dict(screen='magic', phase='target', member=1, row=0, cell=0, target=2))

    def test_live_state_plans_the_verified_recovery(self):
        s = self.menu('ram-menu-item-target.bin')          # Cloud and Tifa KO, one Phoenix Down
        heal = FieldHeal(0.5, Clock())
        self.assertTrue(heal.prepare(s))
        self.assertEqual((s['heal']['step']['item'], s['heal']['step']['target'], s['heal']['input']),
                         (PHOENIX_DOWN, 1, 'confirm'))

    def test_unexpected_cursor_shape_is_not_read_as_item_menu(self):
        path = Path(__file__).parent/'fixtures'/'ram-menu-item-target.bin'
        if not path.exists():
            self.skipTest('Owned-game capture not present.')
        data = bytearray(path.read_bytes())
        data[0x1D3DEE+6] = 0                               # item list no longer 320 rows
        self.assertEqual(read_menu(bytes(data)), dict(screen='other'))


if __name__ == '__main__':
    unittest.main()
