"""Fallback when the route has no step for this field/progress: try the field's
own story triggers, the way a player looks around.

Candidates come from the field script (field_script.story_triggers), never from
guesses:
  1. walk-through LINE triggers that change map (e.g. "follow Hojo" corners)
  2. other walk-through LINE triggers (not door auto-open lines)
  3. talkable people/objects whose talk script does something
  --explore adds: door lines, and talking to everyone once per field even when
  the route does have a step.
Anything that asks a question or opens a menu is tried last and an unverified
choice still stops the run (never answers blindly). Each candidate gets a time
budget; the attempt list resets whenever the field or story progress changes.
"""
import math
import time

from navigation import NoPath, locate

LINE_BUDGET = 12.0
TALK_BUDGET = 10.0


def _walkable(state, line, samples=24):
    mesh = (state.get('navigation') or {}).get('mesh')
    if not mesh:
        return None
    position = state['player']['position']
    best = None
    for i in range(samples+1):
        point = [line[0][k]+(line[1][k]-line[0][k])*i/samples for k in range(3)]
        try:
            locate(mesh, point)
        except NoPath:
            continue
        d = math.dist(point[:2], position[:2])
        if best is None or d < best[0]:
            best = (d, point)
    return best and best[1]


class Explorer:
    def __init__(self, everything=False, clock=time.monotonic):
        self.everything = everything
        self.clock = clock
        self.key = None
        self.tried = set()
        self.current = None      # (candidate id, started, budget)
        self.talked = set()      # --explore: (field, entity) talked to this run

    def _reset(self, key):
        self.key, self.tried, self.current = key, set(), None

    def observe(self, state):
        """Called every tick: dialogue during a talk attempt means it worked."""
        if self.current and state.get('mode') == 'dialog' and self.current[0][0] == 'talk':
            self.tried.add(self.current[0])
            if self.everything:
                self.talked.add((state.get('field_id'), self.current[0][1]))
            self.current = None

    def candidates(self, state, extra_talks=False):
        triggers = state.get('field_triggers') or {}
        actors = {a.get('entity'): a for a in state.get('actors', []) if a.get('talkable')}
        position = state['player']['position']
        found = []
        for line in triggers.get('lines', []):
            door = not any(i in (3, 4) for i in line['scripts']) and not line['ok']
            if door and not self.everything:
                continue
            tier = 3 if line['asks'] else 0 if line['jumps'] else 2 if door else 1
            point = _walkable(state, line['line'])
            if point is None:
                continue
            kind = 'ok_line' if line['ok'] else 'line'
            found.append((tier, math.dist(point[:2], position[:2]), (kind, line['entity']),
                          dict(field_id=state['field_id'], position=point, line=line['line'], radius=0, gateway=-1)
                          if kind == 'line' else
                          dict(field_id=state['field_id'], position=point, line=line['line'], inspect=True, ok_line=True,
                               radius=state['player'].get('collision_radius', 0)+8)))
        for talk in triggers.get('talks', []):
            actor = actors.get(talk['entity'])
            if actor is None:
                continue
            tier = 3 if talk['asks'] else 2
            found.append((tier, actor['distance'], ('talk', talk['entity']),
                          dict(field_id=state['field_id'], position=actor['position'], triangle=actor['triangle'],
                               radius=max(48, actor['collision_radius']+state['player']['collision_radius']+10),
                               actor=actor['entity_name'], entity=talk['entity'])))
        return sorted(found, key=lambda e: (e[0], e[1]))

    def target(self, state):
        key = (state['field_id'], state['story_progress'])
        if key != self.key:
            self._reset(key)
        now = self.clock()
        if self.current and now-self.current[1] > self.current[2]:
            self.tried.add(self.current[0])
            self.current = None
        options = [c for c in self.candidates(state) if c[2] not in self.tried]
        if not options:
            return None, 'Explored every story trigger here without progress; take over or add a route step.'
        tier, _, cid, target = options[0] if not self.current else next(
            (c for c in options if c[2] == self.current[0]), options[0])
        if not self.current or self.current[0] != cid:
            self.current = (cid, now, TALK_BUDGET if cid[0] == 'talk' else LINE_BUDGET)
        label = {'line': 'Walk through a story trigger', 'ok_line': 'Check a story trigger',
                 'talk': f'Talk to {target.get("actor")}'}[cid[0]]
        return dict(target, explore=cid), f'Exploring (no route step): {label}.'

    def talk_first(self, state):
        """--explore: talk to each person once per field before following the route."""
        if not self.everything or state['mode'] != 'field':
            return None, None
        for tier, _, cid, target in self.candidates(state):
            if cid[0] == 'talk' and tier < 3 and (state['field_id'], cid[1]) not in self.talked:
                if self.current and self.current[0] == cid and self.clock()-self.current[1] > TALK_BUDGET:
                    self.talked.add((state['field_id'], cid[1]))
                    self.current = None
                    continue
                if not self.current or self.current[0] != cid:
                    self.current = (cid, self.clock(), TALK_BUDGET)
                return dict(target, explore=cid), f'Explore mode: talk to {target.get("actor")}.'
        return None, None


def with_exploration(explorer, state, target, reason):
    if explorer is None or state['mode'] != 'field' or state.get('movement_locked'):
        return target, reason
    if target is None and (reason or '').startswith('No verified story objective'):
        return explorer.target(state)
    if target is not None and not target.get('explore'):
        talk, why = explorer.talk_first(state)
        if talk:
            return talk, why
    return target, reason
