"""Retreat to a rest spot when healing runs out, and a random-battle test loop.

Rest spots are entities whose field script restores HP/MP (found from the disc
by supplies.build_index). Only spots whose dialogue answers were verified live
are used: REST lists them. Live 2026-10-11, blin64 KLINE ("A nap room..."):
"Let's get some rest" took 558/118, 538/76, 717/125 to 688/142, 609/114,
717/127 for no Gil. Routes follow gateway exits only (supplies.field_path).
"""
import math
import time

from battle_strategy import RESTORE_MATERIA, CURE_MP, ratio
from field_heal import plan
from explore import _walkable
from navigation import NoPath, steering
from route import gateway_by_destination, midpoint
from supplies import load_index, catalogue_names, field_path

REST = {'blin64': dict(entity='KLINE', accept="Let's get some rest", decline="Let's hurry on")}
RESTED = 0.9           # HP and MP share that ends a retreat
PACE_CLEARANCE = 150   # pacing points keep this far from exits
PACE_REACHED = 40


def healers(party):
    return [p for p in party if p.get('hp', 0) > 0 and RESTORE_MATERIA in p.get('materia', ())]


def depleted(state, heal_below, retreat_mp):
    """Why the party should rest, or None. `retreat_mp` is a share of max MP."""
    party = state.get('party', [])
    living = [p for p in party if p.get('hp', 0) > 0]
    if not living:
        return None
    best = max((p['mp'] for p in healers(party)), default=None)
    if best is not None:
        floor = max(2*CURE_MP, retreat_mp*max(p['max_mp'] for p in healers(party)))
        if best < floor:
            return f'healer MP {best} is under {math.ceil(floor)}'
    items = {int(k): v for k, v in state.get('items', {}).items()}
    hurt = [p for p in living if ratio(p) < heal_below]
    if hurt and plan(party, items, heal_below) is None:
        return f'{hurt[0].get("name", "a member")} is under {heal_below:.0%} HP with nothing left to heal with'
    return None


def rested(state):
    living = [p for p in state.get('party', []) if p.get('hp', 0) > 0]
    return all(ratio(p) >= RESTED and p.get('mp', 0) >= RESTED*p.get('max_mp', 0) for p in living)


class Atlas:
    """Lazy disc index + field names shared by the retreat and the grind."""
    def __init__(self, root, index=None, names=None):
        self.root, self.index, self.names = root, index, names

    def load(self, state):
        if self.index is None:
            self.index = load_index(self.root)
        if self.names is None:
            self.names = catalogue_names()
        if state.get('field_name'):
            self.names[state['field_id']] = state['field_name']

    def entry(self, field):
        return self.index.get(self.names.get(field, ''), {})


class Retreat:
    def __init__(self, atlas, heal_below, retreat_mp=0.0):
        self.atlas, self.heal_below, self.retreat_mp = atlas, heal_below, retreat_mp
        self.reset()

    def reset(self):
        self.why = self.failed = None
        self.rests = 0
        self.dead_exits = set()

    def update(self, state):
        """Latch: start on depletion, end once the party is actually restored."""
        if state.get('mode') not in ('field', 'dialog', 'menu'):
            return self.why
        if self.why and rested(state):
            self.why, self.rests = None, self.rests+1
        elif not self.why and not self.failed:
            self.why = depleted(state, self.heal_below, self.retreat_mp)
        return self.why

    def answer(self, state):
        """Rest-spot dialogue: accept only while retreating, otherwise decline."""
        spot = REST.get(state.get('field_name'))
        if not spot or state.get('mode') != 'dialog':
            return None
        for dialog in state['dialogs']:
            lines = [line.strip(' ?"') for line in dialog['text'].splitlines()]
            if dialog['choice_pointer'] and spot['accept'] in lines and spot['decline'] in lines:
                return spot['accept'] if self.why else spot['decline']
        return None

    def target(self, state):
        """(target, reason) toward the nearest verified rest spot while retreating."""
        if not self.update(state) or state.get('mode') != 'field' or state.get('movement_locked') or state.get('fading'):
            return None, None
        try:
            self.atlas.load(state)
        except (OSError, ValueError) as exc:
            return self.fail(f'Rest spots unknown: {exc}')
        here = state['field_id']
        spot = REST.get(self.atlas.names.get(here))
        if spot:
            line = next((l for l in (state.get('field_triggers') or {}).get('lines', []) if l['name'] == spot['entity']), None)
            point = line and _walkable(state, line['line'])
            if point:
                return (dict(field_id=here, position=point, line=line['line'], radius=0, gateway=-1, rest=True),
                        f'Rest ({self.why}): walk into {spot["entity"]}.')
            return self.fail('The rest spot in this field cannot be reached.')
        while True:
            path = field_path(state, self.atlas.index, self.atlas.names, self.dead_exits,
                              lambda field: self.atlas.names.get(field) in REST)
            if path is None:
                return self.fail('No verified rest spot is reachable through field exits.')
            try:
                target = gateway_by_destination(state, path[1])
            except NoPath:
                self.dead_exits.add((here, path[1]))
                continue
            return dict(target, rest=True), f'Rest ({self.why}): go to {self.atlas.names[path[-1]]}, {len(path)-1} field(s) away.'

    def fail(self, reason):
        self.failed, self.why = reason, None
        return None, None


class Grind:
    """Seek random battles: go to the nearest encounter field and pace across it."""
    def __init__(self, atlas, clock=time.monotonic):
        self.atlas, self.clock = atlas, clock
        self.battles = 0
        self.in_battle = False
        self.reset()

    def reset(self):
        self.failed = None
        self.anchor = None          # (field id, point)
        self.since = None
        self.dead_exits = set()

    def observe(self, state):
        fighting = state.get('mode') == 'battle'
        self.battles += int(fighting and not self.in_battle)
        self.in_battle = fighting

    def pace_point(self, state, away_from):
        exits = [midpoint(g['line']) for g in state.get('gateways', [])]
        centers = []
        for triangle in state['navigation']['mesh']:
            if triangle['locked']:
                continue
            center = [sum(v[k] for v in triangle['vertices'])/3 for k in range(3)]
            if all(math.dist(center[:2], e[:2]) >= PACE_CLEARANCE for e in exits):
                centers.append(center)
        for center in sorted(centers, key=lambda c: -math.dist(c[:2], away_from[:2]))[:12]:
            try:
                steering(state, dict(field_id=state['field_id'], position=center, radius=PACE_REACHED))
                return center
            except NoPath:
                continue
        return None

    def target(self, state):
        if state.get('mode') != 'field' or state.get('movement_locked') or state.get('fading') or self.failed:
            return None, None
        try:
            self.atlas.load(state)
        except (OSError, ValueError) as exc:
            self.failed = f'Encounter fields unknown: {exc}'
            return None, None
        here, position = state['field_id'], state['player']['position']
        if self.atlas.entry(here).get('encounters') and state.get('encounters_on', True):
            now = self.clock()
            reached = self.anchor and math.dist(position[:2], self.anchor[1][:2]) <= PACE_REACHED
            if not self.anchor or self.anchor[0] != here or reached or now-self.since > 25:
                point = self.pace_point(state, position)
                if point is None:
                    self.failed = 'No pacing point away from the exits in this field.'
                    return None, None
                self.anchor, self.since = (here, point), now
            return (dict(field_id=here, position=self.anchor[1], radius=PACE_REACHED, grind=True),
                    f'Grind: pace across the field for a random battle ({self.battles} fought).')
        while True:
            # Among the nearest fields prefer the busier encounter table.
            path = field_path(state, self.atlas.index, self.atlas.names, self.dead_exits,
                              lambda field: self.atlas.entry(field).get('encounters'))
            if path is None:
                self.failed = 'No field with random encounters is reachable through field exits.'
                return None, None
            rivals = [d for d in {g['destination'] for g in state.get('gateways', [])}
                      if len(path) == 2 and (here, d) not in self.dead_exits]
            goal = max(rivals, key=lambda d: self.atlas.entry(d).get('encounters', 0), default=path[1]) if len(path) == 2 else path[1]
            try:
                target = gateway_by_destination(state, goal)
            except NoPath:
                self.dead_exits.add((here, goal))
                continue
            return dict(target, grind=True), f'Grind: go to {self.atlas.names.get(path[-1] if len(path) > 2 else goal)} for random battles.'
