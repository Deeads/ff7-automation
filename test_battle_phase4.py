"""Phase 4: battle decode extensions, validation, strategy, menu navigation, probe.

Synthetic data only; no pad or window is used.
"""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy

from battle import decode_battle, battle_choices, validate_players, OVERLAY_SIGNATURE, LIMIT_MASK
from battle_strategy import choose, POTION, HI_POTION, PHOENIX_DOWN, RESTORE_MATERIA
import battle_menu
from battle_menu import next_input, step_towards
import battle_probe
from ff7_state import decode

ROOT = Path(__file__).parent


def raw_battle(players=((500, 700),), enemies=((150, 150),), limit=0, escape=0):
    data = bytearray(0x200000)
    data[0xA0000:0xA0010] = OVERLAY_SIGNATURE
    pmask = sum(1 << i for i in range(len(players)))
    emask = sum(1 << (4+i) for i in range(len(enemies)))
    struct.pack_into('<HH', data, 0xF83BC, pmask, emask)
    for slot, (hp, mx) in [(i, u) for i, u in enumerate(players)]+[(4+i, u) for i, u in enumerate(enemies)]:
        struct.pack_into('<HHII', data, 0xF83E0+slot*0x68+0x28, 10, 20, hp, mx)
        struct.pack_into('<I', data, 0xF83E0+slot*0x68+4, 8)
    struct.pack_into('<H', data, LIMIT_MASK, limit)
    struct.pack_into('<H', data, 0xF7DB2, escape)
    return data


def battle(players, enemies, escapable=True, limit_slots=()):
    return dict(players=[dict(slot=i, hp=hp, max_hp=mx, mp=mp, alive=hp > 0, limit_ready=i in limit_slots)
                         for i, (hp, mx, mp) in enumerate(players)],
                enemies=[dict(slot=4+i, hp=hp, max_hp=mx, alive=hp > 0) for i, (hp, mx) in enumerate(enemies)],
                escapable=escapable)


PARTY = [dict(materia=[52]), dict(materia=[RESTORE_MATERIA]), dict(materia=[])]


class DecodeTests(unittest.TestCase):
    def test_limit_and_escape_flags(self):
        b = decode_battle(raw_battle(players=((500, 700), (300, 600)), limit=0b10, escape=9))
        self.assertEqual([p['limit_ready'] for p in b['players']], [False, True])
        self.assertFalse(b['escapable'])
        self.assertEqual(len(bytes.fromhex(b['raw'][0])), 0x68)
        self.assertTrue(decode_battle(raw_battle(escape=1))['escapable'])

    def test_validation_against_save_records(self):
        b = decode_battle(raw_battle(players=((283, 688), (228, 590), (12, 725))))
        party = [dict(max_hp=688), dict(max_hp=590), dict(max_hp=725)]
        self.assertEqual(validate_players(b, party), [])
        party[2]['max_hp'] = 700
        self.assertEqual(len(validate_players(b, party)), 1)

    def test_inventory_and_materia_from_fixture(self):
        path = ROOT/'fixtures/ram-248-outer.bin'
        if not path.exists():
            self.skipTest('fixture unavailable')
        state = decode(path.read_bytes())
        self.assertEqual((state['items'][POTION], state['items'][HI_POTION], state['items'][PHOENIX_DOWN]), (4, 2, 3))
        self.assertIn(RESTORE_MATERIA, state['party'][1]['materia'])      # Tifa
        self.assertTrue(all(0 <= p['limit_bar'] <= 255 for p in state['party']))


class StrategyTests(unittest.TestCase):
    def test_attack_lowest_hp_enemy(self):
        a = choose(battle([(500, 700, 10)]*3, [(90, 150), (40, 150), (0, 150)]), 0, PARTY, {})
        self.assertEqual((a.command, a.target_slot), ('attack', 5))

    def test_emergency_heal_before_revival(self):
        a = choose(battle([(500, 700, 10), (0, 600, 0), (100, 700, 0)], [(90, 150)]), 0, PARTY, {PHOENIX_DOWN: 1, POTION: 3})
        self.assertEqual((a.command, a.item, a.target_side, a.target_slot), ('item', POTION, 'ally', 2))

    def test_revive_when_survivors_are_not_in_immediate_danger(self):
        a = choose(battle([(500, 700, 10), (0, 600, 0), (400, 700, 0)], [(90, 150)]), 0, PARTY, {PHOENIX_DOWN: 1})
        self.assertEqual((a.command, a.item, a.target_slot), ('item', PHOENIX_DOWN, 1))

    def test_cure_with_restore_and_mp_else_potion(self):
        low = battle([(500, 700, 10), (400, 600, 20), (200, 700, 0)], [(90, 150)])
        self.assertEqual(choose(low, 1, PARTY, {POTION: 1}).command, 'magic')           # Tifa has Restore
        self.assertEqual(choose(low, 0, PARTY, {POTION: 1}).command, 'item')           # no Restore on Cloud
        no_mp = battle([(500, 700, 10), (400, 600, 2), (200, 700, 0)], [(90, 150)])
        self.assertEqual(choose(no_mp, 1, PARTY, {POTION: 1}).item, POTION)
        deep = battle([(500, 700, 10), (400, 600, 2), (50, 700, 0)], [(90, 150)])
        self.assertEqual(choose(deep, 0, PARTY, {POTION: 1, HI_POTION: 1}).item, HI_POTION)

    def test_limit_when_ready(self):
        a = choose(battle([(500, 700, 10)]*3, [(90, 150)], limit_slots=(2,)), 2, PARTY, {})
        self.assertEqual(a.command, 'limit')

    def test_flee_only_escapable_critical_without_heal(self):
        crit = [(500, 700, 10), (100, 600, 0), (100, 700, 0)]
        self.assertEqual(choose(battle(crit, [(90, 150)]), 0, PARTY, {}).command, 'flee')
        self.assertEqual(choose(battle(crit, [(90, 150)], escapable=False), 0, PARTY, {}).command, 'attack')
        self.assertEqual(choose(battle(crit, [(90, 150)]), 0, PARTY, {}, boss=True).command, 'attack')
        self.assertEqual(choose(battle(crit, [(90, 150)]), 0, PARTY, {POTION: 2}).command, 'item')


class MenuTests(unittest.TestCase):
    PHASES = dict(command=1, submenu=2, target=3)

    def menu(self, phase, **kw):
        base = dict(actor=0, phase=self.PHASES[phase], phases=self.PHASES, command_cursor=0,
                    command_ids=[1, 2, 4], submenu_cursor=0, submenu_ids=[], target_cursor=4)
        base.update(kw)
        return base

    def test_unverified_map_refuses(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'m.json'
            path.write_text(json.dumps(dict(verified=False)))
            self.assertIsNone(battle_menu.load_map(path))
        from battle_strategy import Action
        self.assertIsNone(next_input(None, Action('attack', target_slot=4))[0])

    def test_attack_then_target(self):
        from battle_strategy import Action
        action = Action('attack', 'enemy', 5)
        self.assertEqual(next_input(self.menu('command'), action)[0], 'confirm')
        self.assertEqual(next_input(self.menu('command', command_cursor=2), action)[0], 'up')
        self.assertEqual(next_input(self.menu('target', target_cursor=4), action)[0], 'right')
        self.assertEqual(next_input(self.menu('target', target_cursor=5), action)[0], 'confirm')

    def test_item_submenu_and_missing_item(self):
        from battle_strategy import Action
        action = Action('item', 'ally', 1, item=PHOENIX_DOWN)
        self.assertEqual(next_input(self.menu('command'), action)[0], 'down')
        self.assertEqual(next_input(self.menu('command', command_cursor=2), action)[0], 'confirm')
        self.assertEqual(next_input(self.menu('submenu', submenu_ids=[0, 7]), action)[0], 'down')
        self.assertEqual(next_input(self.menu('submenu', submenu_ids=[0, 1]), action)[0], 'cancel')

    def test_grid_steps(self):
        self.assertEqual(step_towards(0, 3, 2), 'down')
        self.assertEqual(step_towards(2, 3, 2), 'right')
        self.assertIsNone(step_towards(3, 3, 2))

    def test_assist_uses_strategy_when_menu_verified(self):
        b = battle([(500, 700, 10)]*3, [(90, 150), (40, 150)])
        b['menu'] = self.menu('command')
        choices, reason = battle_choices(dict(battle=b, party=PARTY, items={}), True)
        self.assertEqual(set(choices), {'wait', 'confirm'})
        self.assertTrue(reason.startswith('attack'))
        self.assertEqual(set(battle_choices(dict(battle=b, party=PARTY, items={}), False)[0]), {'wait'})


class ProbeTests(unittest.TestCase):
    def test_cursor_byte_found_and_noise_rejected(self):
        state = dict(cursor=0, noise=0)
        def fake_ram(source):
            state['noise'] += 1
            arr = numpy.zeros(0x200000, numpy.uint8)
            arr[0x1000] = state['cursor']
            arr[0x2000] = state['noise'] % 256      # timer: changes without input
            return arr

        class Pad:
            pressed = []
            def hold(self, buttons, seconds, stopped):
                self.pressed.append(buttons)
                state['cursor'] += {'down': 1, 'up': -1, 'right': 1, 'left': -1}[buttons]
            def perform(self, action, stopped=None):
                self.pressed.append(action)
                return dict(sent=True)
            def release(self):
                self.pressed.append('release')
        pad = Pad()
        with patch.object(battle_probe, 'ram', side_effect=fake_ram), patch.object(battle_probe.time, 'sleep'):
            report = battle_probe.probe(object(), pad, lambda: False, trials=2)
        self.assertEqual([c['address'] for c in report['command_cursor']], [0x1000])
        self.assertEqual([c['address'] for c in report['target_cursor']], [0x1000])
        self.assertEqual(pad.pressed[-3:], ['cancel', 'cancel', 'release'])   # always backs out


if __name__ == '__main__':
    unittest.main()


class GhostEnemyTests(unittest.TestCase):
    def test_flags_gate_targeting(self):
        data = raw_battle(enemies=((150, 150), (10, 10)))
        struct.pack_into('<I', data, 0xF83E0+5*0x68+4, 0x82)          # helper part: never enabled
        b = decode_battle(data)
        self.assertEqual([e['targetable'] for e in b['enemies']], [True, False])
        struct.pack_into('<I', data, 0xF83E0+4*0x68+4, 0x4008)        # invisible
        self.assertFalse(decode_battle(data)['enemies'][0]['targetable'])

    def test_replay_live_hammer_blaster_log_never_confirms_on_ghost(self):
        path = ROOT/'fixtures/battle-log-429-hammer-blaster.jsonl'
        if not path.exists():
            self.skipTest('live log fixture unavailable')
        confirms_while_ghost = ghost_samples = 0
        for line in path.read_text().splitlines():
            row = json.loads(line)
            enemies = []
            for e in row['enemies']:
                flags = struct.unpack_from('<I', bytes.fromhex(row['raw'][str(e['slot'])]), 4)[0]
                enemies.append(dict(e, targetable=e['alive'] and bool(flags & 8) and not flags & 0x4000))
            b = dict(players=row['players'], enemies=enemies, escape_difficulty=row['escape'], menu_state=1)
            choices, _ = battle_choices(dict(battle=b, flee_exhausted=True), True)
            if not any(e['targetable'] for e in enemies):
                ghost_samples += 1
                confirms_while_ghost += 'confirm' in choices
        self.assertGreater(ghost_samples, 15)          # the 30 s retracted window was captured
        self.assertEqual(confirms_while_ghost, 0)

    def test_ghost_with_open_target_menu_backs_out(self):
        b = decode_battle(raw_battle(enemies=((150, 150),)))
        b['enemies'][0]['targetable'] = False
        self.assertEqual(set(battle_choices(dict(battle=b), True)[0]), {'wait'})
        self.assertEqual(set(battle_choices(dict(battle=b, battle_target_open=True), True)[0]), {'wait', 'cancel'})

    def test_strategy_waits_on_ghost_but_still_heals(self):
        b = battle([(500, 700, 10)]*3, [(90, 150)])
        b['enemies'][0]['targetable'] = False
        self.assertEqual(choose(b, 0, PARTY, {}).command, 'wait')
        b['players'][2]['hp'] = 100
        self.assertEqual(choose(b, 0, PARTY, {POTION: 1}).command, 'item')


class PostBattleTests(unittest.TestCase):
    def run_loop(self, hp_after, extra=(), battle_hp=None):
        import laya_loop
        from ff7_state import StateError, SAVE
        raw = bytearray(0x200000)
        raw[SAVE+0x4F8:SAVE+0x4FB] = bytes([0, 2, 1])
        for i, char in enumerate((0, 2, 1)):
            struct.pack_into('<H', raw, SAVE+0x54+char*132+0x2C, hp_after[i])
        b = decode_battle(raw_battle(players=battle_hp or ((500, 700),)))
        battle_state = dict(pid=1, mode='battle', field_id=248, story_progress=264, party=[dict(name='C', hp=1, max_hp=700)],
                            battle=b, dialogs=[], player=None, captured_monotonic=0)
        field_state = dict(battle_state, mode='field', battle=None, movement_locked=True, fading=False,
                           navigation={'direction_vectors': {}})
        script = [battle_state, battle_state] + [StateError('results')]*6 + [field_state]*3

        stop_file = []

        class Source:
            pid = 1
            calls = 0
            def snapshot(self, attempts=100):
                Source.calls += 1
                if Source.calls > 14 and stop_file:
                    stop_file[0].write_text('stop')
                item = script.pop(0) if len(script) > 1 else script[0]
                if isinstance(item, Exception):
                    raise item
                return dict(item, captured_monotonic=__import__('time').monotonic())
            def raw(self):
                return bytes(raw)
            def close(self):
                pass

        class Pad:
            def __init__(self): self.presses = []
            def perform(self, action, **kw):
                self.presses.append(action); return dict(sent=True, foreground_unchanged=True)
            def release(self): pass
        pad = Pad()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stop_file.append(root/'STOP')
            fake_sleep = lambda seconds: None
            with patch.object(laya_loop, 'ROOT', root), patch.object(laya_loop, 'STOP', root/'STOP'), \
                 patch.object(laya_loop, 'LiveRAM', return_value=Source()), patch.object(laya_loop, 'Controller', return_value=pad), \
                 patch.object(laya_loop, 'LayaPolicy'), patch.object(laya_loop.time, 'sleep', side_effect=fake_sleep), \
                 patch.object(laya_loop, 'POST_BATTLE_PRESS', 0.0), patch('builtins.print'):
                laya_loop.main(['--execute', '--seconds', '0', *extra])
            status = json.loads((root/'laya-status.json').read_text())
            log = (root/'decisions.jsonl').read_text() if (root/'decisions.jsonl').exists() else ''
        return pad, status, log

    def test_results_screens_are_advanced_with_circle(self):
        pad, status, log = self.run_loop([300, 0, 200])
        self.assertGreaterEqual(pad.presses.count('confirm'), 3)
        self.assertIn('post_battle_done', log)

    def test_game_over_without_a_save_state_presses_nothing_and_blocks(self):
        pad, status, log = self.run_loop([0, 0, 0])
        self.assertEqual([p for p in pad.presses if p != 'wait'], [])
        self.assertEqual((status['status'], status['mode']), ('blocked', 'game_over'))
        self.assertIn('game_over_blocked', log)


class LowHpStopTests(unittest.TestCase):
    def test_threshold(self):
        from laya_loop import low_party
        party = [('Cloud', 501, 688), ('Tifa', 128, 590), ('Barret', 378, 725)]
        self.assertIsNone(low_party(party, 0))                 # off
        self.assertIsNone(low_party(party, 20))                # Tifa is 21.7%
        self.assertEqual(low_party(party, 25)[0], 'Tifa')
        self.assertEqual(low_party([('Tifa', 0, 590)], 1)[0], 'Tifa')   # KO counts

    def test_loop_stops_in_battle_without_pressing(self):
        pad, status, log = PostBattleTests().run_loop([300, 200, 200], extra=['--stop-hp', '30'],
                                                      battle_hp=((120, 700),))
        self.assertEqual((status['status'], status['mode']), ('blocked', 'low_hp'))
        self.assertIn('below 30%', status['reason'])
        self.assertEqual([p for p in pad.presses if p != 'wait'], [])
        self.assertIn('low_hp_stop', log)

    def test_default_is_off(self):
        pad, status, log = PostBattleTests().run_loop([300, 200, 200], battle_hp=((120, 700),))
        self.assertNotEqual(status.get('mode'), 'low_hp')
