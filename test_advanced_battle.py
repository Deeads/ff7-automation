import copy
import time
import unittest
from pathlib import Path

from battle_strategy import choose, Action, elemental_multiplier
from battle_control import BattleControl, valid
from battle_menu import read_menu, next_input
from battle import decode_battle
from test_battle_phase4 import battle, PARTY
from laya_loop import guard


def state():
    b = battle([(500, 700, 20), (150, 600, 20), (500, 700, 20)], [(300, 300)])
    b.update(id=1, menu_state=1, menu=dict(actor=0, phase=1, phases=dict(command=1, submenu=6, target=0),
        command_cursor=0, command_ids=[1, 2, 4], submenu_cursor=0, submenu_ids=[0, 33], target_cursor=4))
    b['players'][0]['abilities'] = [dict(id=0, kind='heal', name='Cure', mp=5, potency=5),
        dict(id=33, kind='damage', name='Bolt', mp=4, potency=8, elements=4)]
    b['enemies'][0].update(affinities_known=True, affinities={'2': 'weak'})
    return dict(mode='battle', pid=12, battle=b, party=copy.deepcopy(PARTY), items={}, captured_monotonic=time.monotonic())


class AdvancedStrategyTests(unittest.TestCase):
    def choose(self, s): return choose(s['battle'], 0, s['party'], s['items'])

    def test_limit_replacing_attack_uses_effective_neutral_spell(self):
        s=state(); s['battle']['players'][1]['hp']=500
        s['battle']['players'][0]['commands']=[20,2,4]
        s['battle']['enemies'][0]['affinities']={}
        self.assertEqual(self.choose(s).spell,33)
        s['battle']['enemies'][0]['affinities']={'2':'absorb'}
        self.assertEqual(self.choose(s).command,'switch')

    def test_emergency_switches_to_healer_before_flee(self):
        s=state(); s['battle']['players'][1]['hp']=100
        s['battle']['players'][1]['abilities']=[dict(id=0,kind='heal',mp=5)]
        s['battle']['players'][0]['abilities']=[]
        self.assertEqual(self.choose(s).command,'switch')

    def test_unverified_scrolling_and_revival_are_not_selected(self):
        s=state(); s['battle']['players'][1]['hp']=500
        s['battle']['menu'].update(native=True,navigable=True,capabilities=['attack','magic'])
        s['battle']['players'][0]['abilities']=[dict(id=33,kind='damage',mp=4,elements=4,menu_index=10)]
        c=BattleControl(); c.prepare(s)
        self.assertEqual(c.plan.command,'attack')

    def test_attack_invalidated_when_replaced_by_limit(self):
        s=state();s['battle']['players'][0]['commands']=[20,2,4]
        self.assertFalse(valid(Action('attack','enemy',4),s))

    def test_heal_before_weakness(self):
        self.assertEqual(self.choose(state()).spell, 0)

    def test_weakness_when_healthy(self):
        s=state(); s['battle']['players'][1]['hp']=500
        self.assertEqual((self.choose(s).command, self.choose(s).spell), ('magic', 33))

    def test_absorb_immunity_resist_unknown_do_not_spend_mp(self):
        for response in ('absorb','recover','immune','resist','unknown'):
            s=state();s['battle']['players'][1]['hp']=500
            s['battle']['enemies'][0]['affinities']['2']=response
            self.assertEqual(self.choose(s).command, 'attack')
        self.assertIsNone(elemental_multiplier(3, dict(affinities_known=True)))

    def test_keep_mp_for_emergency_healing(self):
        s=state();s['battle']['players'][1]['hp']=500;s['battle']['players'][0]['mp']=8
        self.assertEqual(self.choose(s).command, 'attack')

    def test_silenced_healer_uses_item(self):
        s=state();s['battle']['players'][0]['silenced']=True;s['items']={0:2}
        self.assertEqual(self.choose(s).command, 'item')

    def test_reflected_ally_uses_item(self):
        s=state();s['battle']['players'][1]['reflect']=True;s['items']={0:2}
        self.assertEqual(self.choose(s).command, 'item')

    def test_authoritative_empty_spell_list_does_not_invent_cure(self):
        s=state();s['party'][0]['materia']=[53];s['battle']['players'][0]['abilities']=[]
        self.assertNotEqual(self.choose(s).command,'magic')

    def test_last_survivor_heals_before_reviving(self):
        s=state();s['battle']['players'][0]['hp']=20
        for p in s['battle']['players'][1:]:p.update(hp=0,alive=False)
        s['items']={7:3}
        self.assertEqual((self.choose(s).spell,self.choose(s).target_slot),(0,0))

    def test_no_generic_enemy_limit_for_unknown_limit_effect(self):
        s=state();s['battle']['players'][1]['hp']=500;s['battle']['players'][0]['limit_ready']=True
        self.assertNotEqual(self.choose(s).command,'limit')

    def test_cures_present_status_only(self):
        s=state();s['battle']['players'][1]['hp']=500;s['battle']['players'][1]['status']=8
        s['battle']['players'][0]['abilities']=[dict(id=4,kind='cure_status',mp=3,removes=8)]
        self.assertEqual(self.choose(s).spell,4)

    def test_pending_heal_not_duplicated(self):
        s=state()
        a=choose(s['battle'],0,s['party'],{},reserved=[('heal',1)])
        self.assertNotEqual(a.spell,0)


class TransactionTests(unittest.TestCase):
    def test_plan_survives_submenu_until_target_confirmation(self):
        s=state();c=BattleControl();c.prepare(s)
        self.assertEqual(c.plan.spell,0)
        s['battle']['menu']['phase']=6;s['battle']['players'][1]['hp']=450
        c.prepare(s)
        self.assertEqual(c.plan.spell,0)
        s['battle']['menu'].update(phase=0,target_cursor=1)
        c.prepare(s);self.assertEqual(s['battle']['control_input'],'confirm')
        c.applied(s,'confirm',True);c.prepare(s)
        self.assertIsNone(s['battle']['control_input'])
        self.assertEqual(len(c.pending),1)

    def test_dead_target_cancels_attack(self):
        s=state();s['battle']['players'][1]['hp']=500;c=BattleControl();c.prepare(s)
        s['battle']['menu']['phase']=0;s['battle']['enemies'][0].update(hp=0,alive=False)
        c.prepare(s);self.assertEqual(s['battle']['control_input'],'cancel')

    def test_silence_mid_selection_cancels_spell(self):
        s=state();c=BattleControl();c.prepare(s)
        s['battle']['menu']['phase']=6;s['battle']['players'][0]['silenced']=True
        c.prepare(s);self.assertEqual(s['battle']['control_input'],'cancel')

    def test_target_search_tries_other_directions_when_right_stalls(self):
        c=BattleControl();c.edges[(4,'right')]=4
        self.assertEqual(c.target_step(4,1),'left')
        c.moves=32;self.assertIsNone(c.target_step(4,1))

    def test_guard_rejects_cursor_change_even_when_same_button_would_be_offered(self):
        before=state();after=copy.deepcopy(before)
        after['battle']['menu']['target_cursor']=1
        self.assertIn('cursor',guard(before,after,'confirm',2,battle_assist=True))

    def test_wrong_selected_spell_is_cancelled(self):
        menu=dict(native=True,navigable=True,phase=0,phases=dict(command=1,submenu=6,target=0),
                  selected_command=2,selected_ability=33,target_cursor=1)
        self.assertEqual(next_input(menu,Action('magic','ally',1,spell=0))[0],'cancel')

    def test_new_encounter_clears_old_reservations(self):
        s=state();c=BattleControl();c.prepare(s);c.pending=[dict(kind='heal',slot=1,hp=150,status=0,time=0)]
        s['battle']['id']=2;c.prepare(s);self.assertEqual(c.pending,[])

    def test_wait_does_not_confirm_target(self):
        s=state();s['battle']['menu'].update(phase=0,target_cursor=-1)
        self.assertIsNone(next_input(s['battle']['menu'],Action('wait'))[0])


class OwnedCaptureTests(unittest.TestCase):
    def fixture(self,name):
        p=Path(__file__).parent/'fixtures'/name
        if not p.exists():self.skipTest('Owned-game capture not present.')
        return p.read_bytes()

    def test_active_spells_and_enemy_weakness(self):
        b=decode_battle(self.fixture('ram-battle-ready.bin'))
        self.assertEqual([[a['id'] for a in p['abilities']] for p in b['players']],[[33],[0],[27]])
        self.assertEqual(b['enemies'][0]['affinities']['2'],'weak')

    def test_native_cursor_uses_per_actor_structure_and_actual_target(self):
        data=self.fixture('ram-battle-cure-target.bin')
        menu=read_menu(data,dict(adapter='ff7-us-native-v1'))
        self.assertEqual((menu['actor'],menu['command_cursor'],menu['selected_ability']),(1,1,0))
        self.assertEqual(menu['target_cursor'],data[0xF38A9])


if __name__=='__main__':unittest.main()
