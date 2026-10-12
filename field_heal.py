"""Recovery between battles through the main menu; never sends input.

`plan` is pure. `FieldHeal` turns it into one feedback-checked menu input per
tick and watches the save-record HP for the outcome, so an input that had no
effect is not repeated indefinitely.
"""
from dataclasses import dataclass, asdict, replace
import time

from battle_strategy import POTION, HI_POTION, PHOENIX_DOWN, RESTORE_MATERIA, CURE_MP, ratio
from field_menu import next_input, commits

TOP_UP = 0.75          # once the menu is open, restore everyone to this buffer
OUTCOME_WAIT = 1.5     # seconds for the HP change to appear in the save record
MAX_INPUTS = 80        # per menu visit
SETTLE = 0.35          # after an input, before the menu is read for the next one


@dataclass(frozen=True)
class Step:
    kind: str             # item | magic
    target: int           # party slot
    item: int = -1
    caster: int = -1
    reason: str = ''


def plan(party, items, below, magic=True):
    """Next recovery step for save-record `party`, or None.

    Revive first (a KO healer before anyone else), then the most hurt member
    under `below`: Cure while the caster keeps one cast for the next battle,
    otherwise the smaller potion that covers the missing HP.
    """
    members = [(slot, p) for slot, p in enumerate(party) if p.get('max_hp')]
    living = [m for m in members if m[1].get('hp', 0) > 0]
    if not living:
        return None
    healer = lambda p: RESTORE_MATERIA in p.get('materia', ())
    name = lambda slot: party[slot].get('name') or f'slot {slot}'
    if items.get(PHOENIX_DOWN, 0) > 0:
        dead = sorted((m for m in members if m[1].get('hp', 0) <= 0), key=lambda m: (not healer(m[1]), m[0]))
        if dead:
            return Step('item', dead[0][0], item=PHOENIX_DOWN, reason=f'Revive {name(dead[0][0])} with a Phoenix Down.')
    for slot, member in sorted((m for m in living if ratio(m[1]) < below), key=lambda m: ratio(m[1])):
        health = f'{member["hp"]}/{member["max_hp"]}'
        casters = [m for m in living if healer(m[1]) and m[1].get('mp', 0) >= 2*CURE_MP]
        if magic and casters:
            caster = max(casters, key=lambda m: m[1]['mp'])[0]
            return Step('magic', slot, caster=caster, reason=f'{name(caster)} casts Cure on {name(slot)} ({health}).')
        for item in (POTION, HI_POTION) if member['max_hp']-member['hp'] <= 150 else (HI_POTION, POTION):
            if items.get(item, 0) > 0:
                return Step('item', slot, item=item, reason=f'Use a potion on {name(slot)} ({health}).')
    return None


class FieldHeal:
    def __init__(self, below, clock=time.monotonic):
        self.below, self.clock = below, clock
        self.scope = None
        self.reset()

    def reset(self):
        self.visit = False      # the menu is open for recovery
        self.failed = None
        self.pending = None
        self.opens = self.inputs = self.misses = 0
        self.sent_at = -SETTLE

    def fail(self, reason):
        self.failed = reason
        self.pending = None
        return False

    def prepare(self, state):
        """Put the next recovery input in state['heal']; False when none applies."""
        mode = state.get('mode')
        scope = (state.get('pid'), state.get('connection_epoch'), state.get('field_id'))
        if scope != self.scope or mode == 'battle':
            # A new field or a fight changes HP, MP and stock: old failures are stale.
            self.reset()
            self.scope = scope
        if not self.below or self.failed or mode not in ('field', 'menu'):
            return False
        party = state.get('party', [])
        if mode == 'field':
            if self.visit:
                self.visit, self.inputs, self.opens = False, 0, 0
            if state.get('movement_locked') or state.get('fading'):
                return False
        slots = {int(k): v for k, v in state.get('item_slots', {}).items()}
        access = state.get('menu_access', {})
        usable = lambda entry: access.get('visible', 0) >> entry & 1 and not access.get('locked', 0) >> entry & 1
        items = {int(k): v for k, v in state.get('items', {}).items() if int(k) in slots} if usable(0) else {}
        step = plan(party, items, max(self.below, TOP_UP) if self.visit else self.below, magic=bool(usable(1)))
        if self.pending:
            target = party[self.pending['slot']] if self.pending['slot'] < len(party) else {}
            if target.get('hp', 0) > self.pending['hp']:
                self.pending, self.misses = None, 0
            elif self.clock()-self.pending['time'] < OUTCOME_WAIT:
                return self.offer(state, None, 'Waiting for the recovery to show in the party record.', step)
            else:
                self.pending = None
                self.misses += 1
                if self.misses >= 2:
                    return self.fail('Recovery was confirmed twice without any HP change.')
        if step is None:
            return False
        if self.clock()-self.sent_at < SETTLE:
            # A screen change must be visible before the next press is chosen.
            return self.offer(state, None, 'Letting the menu settle.', step)
        if mode == 'field':
            if self.opens >= 3:
                return self.fail('The menu did not open on this screen.')
            return self.offer(state, 'switch', f'Open the menu: {step.reason}', step)
        self.visit = True
        if self.inputs >= MAX_INPUTS:
            return self.fail('Menu navigation exceeded its input budget.')
        rows = state.get('party_slots') or list(range(len(party)))
        shown = replace(step, target=rows[step.target], caster=rows[step.caster] if step.caster >= 0 else -1)
        return self.offer(state, *next_input(state.get('menu'), shown, slots.get(step.item)), step)

    @staticmethod
    def offer(state, action, reason, step):
        state['heal'] = dict(input=action, reason=reason, step=asdict(step) if step else None)
        return True

    def applied(self, state, action, sent):
        heal = state.get('heal')
        if not sent or not heal or action != heal['input']:
            return
        self.inputs += 1
        self.sent_at = self.clock()
        if state['mode'] == 'field':
            self.opens += 1
        elif commits(state.get('menu'), action):
            slot = heal['step']['target']
            self.pending = dict(slot=slot, hp=state['party'][slot].get('hp', 0), time=self.clock())
