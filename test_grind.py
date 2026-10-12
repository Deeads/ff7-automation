import copy
import unittest
from pathlib import Path

from battle_strategy import choose, Action, POTION, HI_POTION, RESTORE_MATERIA
from battle_control import BattleControl
from battle_menu import next_input, read_menu
from grind import Atlas, Retreat, Grind, depleted, rested
from laya_loop import choices_for
from test_advanced_battle import state as battle_state
from test_battle_phase4 import battle, PARTY
from test_supplies import state as field_state

INDEX = {'here': dict(exits=[11], pickups=[], encounters=0, rests=[]),
         'floor65': dict(exits=[10, 13], pickups=[], encounters=240, rests=[]),
         'blin64': dict(exits=[11], pickups=[], encounters=0, rests=['KLINE'])}
NAMES = {10: 'here', 11: 'floor65', 13: 'blin64'}
NAP = 'Cloud\n"A nap room..."\n          Let\'s get some rest\n          Let\'s hurry on'


def party(tifa_mp=96, red_hp=600):
    return [dict(name='Cloud', hp=600, max_hp=688, mp=100, max_mp=142, materia=[52]),
            dict(name='Tifa', hp=500, max_hp=609, mp=tifa_mp, max_mp=114, materia=[RESTORE_MATERIA]),
            dict(name='Red XIII', hp=red_hp, max_hp=717, mp=100, max_mp=127, materia=[49])]


def field(field_id=10, name='here', exits=(11,), **kw):
    s = field_state(field_id, name, exits, ko=False)
    s.update(party=party(**kw), items={}, encounters_on=True, field_triggers=dict(lines=[], talks=[]))
    return s


class RetreatTests(unittest.TestCase):
    def setUp(self):
        self.retreat = Retreat(Atlas('.', INDEX, dict(NAMES)), 0.5, 0.0)

    def test_depletion_rules(self):
        self.assertIsNone(depleted(field(), 0.5, 0))
        self.assertIn('healer MP 9', depleted(field(tifa_mp=9), 0.5, 0))             # under two Cures
        self.assertIn('healer MP 96', depleted(field(), 0.5, 0.9))                    # configured share
        hurt = field(red_hp=100); hurt['party'][1]['materia'] = []
        self.assertIn('nothing left to heal with', depleted(hurt, 0.5, 0))
        hurt['items'] = {POTION: 1}
        self.assertIsNone(depleted(hurt, 0.5, 0))

    def test_routes_to_the_verified_rest_field_and_latches_until_rested(self):
        target, reason = self.retreat.target(field(tifa_mp=9))
        self.assertEqual(target['gateway'], 0)
        self.assertIn('go to blin64, 2 field(s) away', reason)
        self.assertIsNotNone(self.retreat.target(field(tifa_mp=40))[0])               # still retreating
        full = field()
        for member in full['party']:
            member.update(hp=member['max_hp'], mp=member['max_mp'])
        self.assertTrue(rested(full))
        self.assertEqual(self.retreat.target(full), (None, None))
        self.assertEqual(self.retreat.rests, 1)

    def test_walks_into_the_rest_line_and_answers_by_need(self):
        there = field(13, 'blin64', (11,), tifa_mp=9)
        there['field_triggers'] = dict(lines=[dict(entity=5, name='KLINE', line=[[400, 500, 0], [600, 500, 0]],
                                                   scripts=[5, 7], ok=False, jumps=False, asks=True)], talks=[])
        target, reason = self.retreat.target(there)
        self.assertEqual((target['gateway'], target['rest']), (-1, True))
        ask = dict(there, mode='dialog', dialogs=[dict(id=1, choice_pointer=True, text=NAP, choice_row=3,
                                                       ready=True, auto_close=False)])
        self.assertEqual(self.retreat.answer(ask), "Let's get some rest")
        ask['dialogue_answer'] = self.retreat.answer(ask)
        self.assertEqual(set(choices_for(ask)[0]), {'wait', 'up'})
        self.retreat.why = None
        self.assertEqual(self.retreat.answer(ask), "Let's hurry on")
        self.assertIsNone(self.retreat.answer(dict(ask, field_name='other')))

    def test_no_rest_spot_is_a_stated_failure(self):
        retreat = Retreat(Atlas('.', INDEX, {10: 'here', 11: 'floor65', 13: 'elsewhere'}), 0.5)
        self.assertEqual(retreat.target(field(tifa_mp=9)), (None, None))
        self.assertIn('No verified rest spot', retreat.failed)


class GrindTests(unittest.TestCase):
    def test_goes_to_the_encounter_field_then_paces_and_counts_battles(self):
        grind = Grind(Atlas('.', INDEX, dict(NAMES)), clock=lambda: 0.0)
        target, reason = grind.target(field())
        self.assertEqual(target['gateway'], 0)
        there = field(11, 'floor65', (10, 13))
        target, reason = grind.target(there)
        self.assertIsNone(target.get('gateway'))
        self.assertIn('pace', reason)
        here = there['player']['position']
        self.assertGreater(abs(target['position'][0]-here[0])+abs(target['position'][1]-here[1]), 100)
        for mode in ('field', 'battle', 'battle', 'field', 'battle'):
            grind.observe(dict(mode=mode))
        self.assertEqual(grind.battles, 2)

    def test_encounters_switched_off_live_means_move_on(self):
        grind = Grind(Atlas('.', INDEX, dict(NAMES)))
        there = dict(field(11, 'floor65', (10, 13)), encounters_on=False)
        self.assertEqual(grind.target(there), (None, None))
        self.assertIn('No field with random encounters', grind.failed)


class BattleAuditTests(unittest.TestCase):
    def test_ko_ally_with_healthy_survivors_keeps_fighting(self):
        b = battle([(500, 700, 0), (0, 600, 0), (354, 700, 0)], [(250, 250), (250, 250)])
        b['escapable'] = True
        self.assertEqual(choose(b, 0, PARTY, {}).command, 'attack')

    def test_critical_survivor_flees_unless_enemies_are_nearly_dead(self):
        b = battle([(25, 700, 0), (0, 600, 0), (354, 700, 0)], [(250, 250), (250, 250)])
        b['escapable'] = True
        self.assertEqual(choose(b, 0, PARTY, {}).command, 'flee')
        b = battle([(25, 700, 0), (0, 600, 0), (354, 700, 0)], [(78, 260), (7, 260)])   # live 2026-10-10
        b['escapable'] = True
        self.assertEqual(choose(b, 0, PARTY, {}).command, 'attack')

    def native(self, **menu):
        s = battle_state()
        s['battle']['menu'].update(native=True, navigable=True, capabilities=['attack', 'magic', 'item', 'limit'],
                                   phases=dict(command=1, submenu=6, target=0, item=5, limit=24), command_ids=[1, 2, 4],
                                   item_cursor=0, item_ids=[0xFFFF, 3, 1], item_stock=[0, 11, 2],
                                   selected_command=0, selected_ability=0, **menu)
        return s

    def test_item_navigation_uses_battle_stock_and_checks_the_selection(self):
        menu = self.native()['battle']['menu']
        use = Action('item', 'ally', 1, item=HI_POTION)
        self.assertEqual(next_input(dict(menu, phase=5), use)[0], 'down')
        self.assertEqual(next_input(dict(menu, phase=5, item_cursor=2), use)[0], 'confirm')
        self.assertEqual(next_input(dict(menu, phase=5), Action('item', 'ally', 1, item=POTION))[0], 'cancel')   # used up
        self.assertEqual(next_input(dict(menu, phase=5), Action('attack', 'enemy', 4))[0], 'cancel')
        target = dict(menu, phase=0, selected_command=4, target_cursor=1)
        self.assertEqual(next_input(dict(target, selected_ability=HI_POTION), use)[0], 'confirm')
        self.assertEqual(next_input(dict(target, selected_ability=3), use)[0], 'cancel')

    def test_potion_is_planned_from_the_live_battle_list_not_the_save_record(self):
        s = self.native()
        s['battle']['players'][0]['abilities'] = []
        s['battle']['players'][1]['hp'] = 100
        s['items'] = {POTION: 1}                         # stale: the battle list shows it is gone
        BattleControl().prepare(s)
        plan = s['battle']['planned_action']
        self.assertEqual((plan['command'], plan['item']), ('item', HI_POTION))

    def limit_state(self, char_id, **menu):
        s = self.native(**menu)
        s['battle']['players'][1]['hp'] = 500
        s['battle']['players'][0].update(limit_ready=True, commands=[20, 2, 4])
        s['battle']['menu']['command_ids'] = [20, 2, 4]
        s['party'][0]['id'] = char_id
        return s

    def test_every_character_uses_the_first_limit(self):
        for char_id in (0, 2, 3, 6):                 # Cloud, Tifa, Aeris, Cait Sith
            s = self.limit_state(char_id)
            BattleControl().prepare(s)
            self.assertEqual(s['battle']['planned_action']['command'], 'limit')
        self.assertEqual(next_input(dict(s['battle']['menu'], phase=24), Action('limit', 'enemy', 4))[0], 'confirm')

    def test_support_limit_keeps_the_party_target_the_game_opened_on(self):
        s = self.limit_state(3, phase=0, target_cursor=1)       # Healing Wind: cursor starts on the party
        s['battle']['menu']['selected_command'] = 20
        control = BattleControl()
        control.key = (s.get('pid'), s.get('connection_epoch'), s['battle']['id'], tuple(p['slot'] for p in s['battle']['players']))
        control.actor = 0
        control.plan = Action('limit', 'enemy', 4, reason='Limit Break is ready.')
        control.prepare(s)
        self.assertEqual(s['battle']['control_input'], 'confirm')
        self.assertIn('Support Limit', s['battle']['control_reason'])

    def test_limit_reels_are_stopped_with_circle_and_other_unknown_phases_wait(self):
        s = self.limit_state(2, phase=27)                        # Tifa's slots, live 2026-10-11
        s['battle']['menu']['selected_command'] = 20
        BattleControl().prepare(s)
        self.assertEqual((s['battle']['control_input'], s['battle']['control_reason']), ('confirm', 'Stop the Limit reel with Circle.'))
        idle = self.limit_state(2, phase=27)
        idle['battle']['menu']['selected_command'] = 1           # not a Limit: leave unknown screens alone
        BattleControl().prepare(idle)
        self.assertIsNone(idle['battle']['control_input'])
        between = self.limit_state(2, phase=-1)
        between['battle']['menu']['selected_command'] = 20
        BattleControl().prepare(between)
        self.assertIsNone(between['battle']['control_input'])

    def test_healer_switch_is_bounded(self):
        s = battle_state()
        s['battle']['players'][1]['hp'] = 100
        s['battle']['players'][1]['abilities'] = [dict(id=0, kind='heal', mp=5)]
        s['battle']['players'][0]['abilities'] = []
        control = BattleControl()
        for _ in range(3):
            fresh = copy.deepcopy(s)
            control.prepare(fresh)
            self.assertEqual(fresh['battle']['control_input'], 'switch')
            control.applied(fresh, 'switch', True)
        fresh = copy.deepcopy(s)
        control.prepare(fresh)
        self.assertEqual(fresh['battle']['planned_action']['command'], 'attack')

    def test_hidden_enemies_turn_is_spent_on_recovery_never_on_an_attack(self):
        s = battle_state()
        s['battle']['enemies'][0]['targetable'] = False
        s['battle']['players'][1]['hp'] = 380                     # 63%: above the normal 40% rule
        plan = choose(s['battle'], 0, s['party'], {})
        self.assertEqual((plan.command, plan.target_side, plan.target_slot), ('magic', 'ally', 1))
        s['battle']['players'][1]['hp'] = 500
        self.assertEqual(choose(s['battle'], 0, s['party'], {}).command, 'wait')

    def test_enemy_the_cursor_cannot_select_is_dropped_and_another_is_taken(self):
        clock = [10.0]
        s = self.native(phase=0, target_cursor=5)
        s['battle']['players'][1]['hp'] = 500
        s['battle']['players'][0]['abilities'] = []
        s['battle']['enemies'].append(dict(slot=5, hp=400, max_hp=400, alive=True, targetable=True))
        s['battle']['menu'].update(selected_command=1)
        control = BattleControl(clock=lambda: clock[0])
        control.key = (s.get('pid'), s.get('connection_epoch'), s['battle']['id'], tuple(p['slot'] for p in s['battle']['players']))
        control.actor = 0
        control.plan = Action('attack', 'enemy', 4, reason='Attack slot 4.')
        control.moves = 4                                         # four moves never reached slot 4
        control.prepare(s)
        self.assertEqual(s['battle']['control_input'], 'confirm')
        self.assertIn('slot 5', s['battle']['control_reason'])
        later = self.native()
        later['battle']['players'][1]['hp'] = 500
        later['battle']['players'][0]['abilities'] = []
        later['battle']['enemies'].append(dict(slot=5, hp=400, max_hp=400, alive=True, targetable=True))
        control.plan = None
        control.prepare(later)
        self.assertEqual(later['battle']['planned_action']['target_slot'], 5)       # slot 4 left out
        clock[0] += 9
        again = copy.deepcopy(later)
        again['battle']['enemies'][0].pop('unselectable'); again['battle']['enemies'][0]['targetable'] = True
        control.plan = None
        control.prepare(again)
        self.assertEqual(again['battle']['planned_action']['target_slot'], 4)       # trusted again after 8 s


class OwnedCaptureTests(unittest.TestCase):
    def test_live_item_phase_decodes(self):
        path = Path(__file__).parent/'fixtures'/'ram-battle-item-list.bin'
        if not path.exists():
            self.skipTest('Owned-game capture not present.')
        menu = read_menu(path.read_bytes(), dict(adapter='ff7-us-native-v1'))
        self.assertEqual((menu['phase'], menu['item_cursor'], menu['item_ids'][1], menu['item_stock'][1]), (5, 2, 3, 11))


if __name__ == '__main__':
    unittest.main()
