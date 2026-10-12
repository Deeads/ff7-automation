"""Persistent battle plans and bounded target navigation; never sends input."""
from collections import deque
from dataclasses import asdict
import time

from battle_strategy import choose, Action, PHOENIX_DOWN, elemental_multiplier

# Every character's first Limit is used. Whether it attacks or supports is read
# from the game itself at the target step: a cursor that opens on the party
# (Aeris' Healing Wind) is confirmed as is, one on the enemies takes an enemy.
LIMIT_COMMAND = 20
HIDDEN_AFTER = 4       # cursor moves without reaching the planned enemy before another one is accepted
HIDDEN_SECONDS = 8.0   # how long an unselectable enemy is left out of targeting
from battle_menu import next_input


def valid(plan, state):
    battle = state['battle']; menu = battle.get('menu') or {}
    actor = next((p for p in battle['players'] if p['slot'] == menu.get('actor')), None)
    if not actor or not actor['alive']:
        return False
    if plan.command in ('wait', 'flee', 'switch'):
        return True
    if plan.command == 'attack' and 'commands' in actor and 1 not in actor['commands']:
        return False
    stock = state.get('battle_items', state.get('items', {}))
    if plan.command == 'item' and int(stock.get(plan.item, stock.get(str(plan.item), 0))) <= 0:
        return False
    ability = None
    if plan.command == 'magic':
        ability = next((a for a in actor.get('abilities', []) if a['id'] == plan.spell), None)
        if not ability or not ability.get('available', True) or ability['mp'] > actor['mp'] or actor.get('silenced') or actor.get('magic_blocked'):
            return False
    units = battle['players'] if plan.target_side == 'ally' else battle['enemies']
    target = next((p for p in units if p['slot'] == plan.target_slot), None)
    if target is None:
        return False
    revival = plan.item == PHOENIX_DOWN or (ability or {}).get('kind') == 'revive'
    if revival:
        return not target['alive']
    if not target['alive'] or (plan.target_side == 'enemy' and not target.get('targetable', True)):
        return False
    if plan.command == 'magic' and target.get('reflect'):
        return False
    if ability and ability['kind'] == 'damage':
        return (elemental_multiplier(ability.get('elements', 0), target) or 0) > 0
    return True


class BattleControl:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.reset()

    def reset(self):
        self.key = self.plan = self.actor = self.previous = self.confirmed = None
        self.edges = {}; self.moves = 0; self.pending = []; self.failed = None
        self.switches = 0
        self.unreachable = set()   # (kind, ally slot) whose target cursor could not be reached
        self.hidden = {}           # enemy slot -> time until which the cursor is assumed unable to select it

    def prepare(self, state):
        b = state.get('battle') or {}; menu = b.get('menu')
        if state.get('mode') != 'battle':
            self.reset(); return
        if not menu:
            return
        key = (state.get('pid'), state.get('connection_epoch'), b['id'], tuple(p.get('id', p['slot']) for p in b['players']))
        if key != self.key:
            self.reset(); self.key = key
        phase = menu['phase']; phases = menu['phases']; actor = menu['actor']
        current = menu['target_cursor']
        # Only snapshot feedback creates graph edges, never assumed key effects.
        if self.previous is not None and phase == phases['target']:
            origin, direction = self.previous
            self.edges[(origin, direction)] = current
        self.previous = None
        for pending in list(self.pending):
            target = next((p for p in b['players'] if p['slot'] == pending['slot']), None)
            if target is None or (pending['kind'] == 'revive' and target['alive']) or (
                    pending['kind'] == 'heal' and target['hp'] > pending['hp']) or (
                    pending['kind'] == 'status' and target.get('status', 0) != pending['status']):
                self.pending.remove(pending)
        if any(self.clock()-p['time'] > 20 for p in self.pending):
            return self.set_input(b, None, 'Recovery outcome is not confirmed; inspect the battle before repeating it.')
        if self.confirmed is not None:
            if (actor, phase) == self.confirmed:
                return self.set_input(b, None, 'Waiting for the accepted target confirmation to leave the menu.')
            self.confirmed = None; self.plan = None
        if phase not in phases.values():
            self.plan = None
            if menu.get('native') and phase > 0 and menu.get('selected_command') == LIMIT_COMMAND:
                # Limit input screens after the target is chosen. Live 2026-10-11:
                # phase 27 is Tifa's slot reels, which spin until Circle stops each one.
                return self.set_input(b, 'confirm', 'Stop the Limit reel with Circle.')
            return self.set_input(b, None, 'Waiting for a command menu.')
        if self.actor != actor:
            self.plan = None; self.actor = actor; self.failed = None; self.switches = 0
        # Unit flags can call an enemy targetable while the cursor skips it (live
        # 2026-10-11, Hammer Blaster slot 6). The cursor is the authority for a while.
        now = self.clock()
        self.hidden = {slot: until for slot, until in self.hidden.items() if until > now}
        for enemy in b['enemies']:
            if enemy['slot'] in self.hidden:
                enemy['targetable'] = False
                enemy['unselectable'] = True
        items = {int(k): v for k, v in state.get('items', {}).items()}
        if menu.get('native') and 'item' not in menu.get('capabilities', []):
            items = {}
        elif menu.get('native'):
            # The save record only changes after the fight; the battle's own list is current.
            items = {}
            for item, count in zip(menu['item_ids'], menu['item_stock']):
                if item != 0xFFFF:
                    items[item] = items.get(item, 0)+count
        state['battle_items'] = items
        if menu.get('native'):
            for player, member in zip(b['players'], state.get('party', [])):
                if ('limit' in menu.get('capabilities', []) and player.get('limit_ready')
                        and LIMIT_COMMAND in player.get('commands', [])):
                    player['limit_kind'] = 'damage'
            # Only offer spells whose menu and target behavior are verified.
            for player in b['players']:
                for ability in player.get('abilities', []):
                    ability['available'] = (ability.get('menu_index', 0) < 9 and
                        ability['kind'] != 'revive' and 'magic' in menu.get('capabilities', []))
        candidate = choose(b, actor, state['party'], items, boss=state.get('flee_exhausted', False),
                           reserved={(p['kind'], p['slot']) for p in self.pending} | self.unreachable,
                           # Triangle did nothing three times: nobody else is ready, so act.
                           allow_switch=self.switches < 3)
        if self.plan and (self.plan.command in ('wait', 'switch') or not valid(self.plan, state) or candidate.priority > self.plan.priority and candidate.priority >= 90):
            self.plan = None
            if phase != phases['command']:
                return self.set_input(b, 'cancel', 'Cancel the stale plan before selecting a new action.')
        if self.plan is None:
            if phase != phases['command']:
                return self.set_input(b, 'cancel', 'Return to commands before planning an action.')
            self.plan = candidate; self.edges = {}; self.moves = 0
        plan = self.plan
        b['planned_action'] = asdict(plan)
        direction, reason = next_input(menu, plan)
        if phase == phases['target'] and direction not in ('cancel', None, 'confirm'):
            on_enemy = any(e['slot'] == current and e['alive'] and e.get('targetable', True)
                           and not e.get('physical_immune') and not e.get('counter_physical') for e in b['enemies'])
            # A Limit takes any enemy; so does Attack once the planned one proves hard to select.
            on_party = any(p['slot'] == current for p in b['players'])
            settle = (plan.command == 'limit' and (on_enemy or on_party)
                      or on_enemy and plan.command == 'attack' and self.moves >= HIDDEN_AFTER)
            direction = None if settle else self.target_step(current, plan.target_slot)
            reason = 'Navigate to the verified target.'
            if direction is None:
                # Never idle on an unreachable cursor: an enemy-side action takes
                # the enemy already selected, anything else is replanned.
                if plan.command == 'limit' and on_party:
                    direction, reason = 'confirm', 'Support Limit: confirm its own party target.'
                elif plan.target_side == 'enemy' and current != plan.target_slot and plan.command != 'limit':
                    self.hidden[plan.target_slot] = now+HIDDEN_SECONDS
                if direction:
                    pass
                elif plan.target_side == 'enemy' and on_enemy and plan.command in ('attack', 'limit'):
                    direction, reason = 'confirm', f'Take the selected enemy (slot {current}).'
                else:
                    if plan.target_side == 'ally':
                        target = next((p for p in b['players'] if p['slot'] == plan.target_slot), None)
                        self.unreachable.add(('revive' if target and not target['alive'] else 'heal', plan.target_slot))
                    self.plan = None
                    return self.set_input(b, 'cancel', 'Target cannot be reached; back out and replan.')
        self.set_input(b, direction, f'{reason} Plan: {plan.reason}')

    @staticmethod
    def set_input(battle, direction, reason):
        battle['control_input'] = direction
        battle['control_reason'] = reason

    def target_step(self, start, target):
        if self.moves >= 12:   # live 2026-10-11: a flagged-but-unselectable enemy cost 32 moves (13 s)
            return None
        queue = deque([(start, [])]); seen = {start}
        frontier = None
        while queue:
            node, path = queue.popleft()
            if node == target:
                return path[0] if path else None
            for direction in ('right', 'left', 'up', 'down'):
                nxt = self.edges.get((node, direction))
                if nxt is None:
                    if frontier is None: frontier = path+[direction]
                elif nxt not in seen:
                    seen.add(nxt); queue.append((nxt, path+[direction]))
        return frontier[0] if frontier else None

    def applied(self, state, action, sent):
        if sent and action == 'switch':
            self.switches += 1
        if not sent or not self.plan:
            return
        b = state.get('battle') or {}; menu = b.get('menu')
        if not menu or menu['phase'] != menu['phases']['target']:
            return
        if action in ('up', 'down', 'left', 'right'):
            self.previous = (menu['target_cursor'], action); self.moves += 1
        elif action == 'confirm':
            self.confirmed = (menu['actor'], menu['phase'])
            if self.plan.target_side == 'ally':
                target = next(p for p in b['players'] if p['slot'] == self.plan.target_slot)
                kind = 'revive' if not target['alive'] else 'heal'
                if self.plan.command == 'magic':
                    actor = next(p for p in b['players'] if p['slot'] == menu['actor'])
                    ability = next(a for a in actor['abilities'] if a['id'] == self.plan.spell)
                    if ability['kind'] == 'cure_status': kind = 'status'
                self.pending.append(dict(kind=kind, slot=target['slot'], hp=target['hp'],
                                         status=target.get('status', 0), time=self.clock()))
