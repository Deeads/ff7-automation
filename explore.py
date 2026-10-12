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
choice still stops the run (never answers blindly).

Unsure mode (a route step had no effect, laya_loop): every reachable trigger
line is both walked onto and stood on with Circle, door lines included, and
every visible talkable model is checked, each once per field and progress.
Live 2026-10-11, jail cell blin67_2: the route pressed Circle 121 times beside
the cell door line, whose script only runs when Cloud walks onto it. Each candidate gets a time
budget; the attempt list resets whenever the field or story progress changes.
"""
import math
import time
from collections import Counter

from navigation import NoPath, locate, steering, segment_distance
from route import actor_near, beside_actor

LINE_BUDGET = 12.0
REENTER = 70        # a contact line only fires on entry: step this far away before walking back
MAX_YIELDS = 4      # a candidate that keeps producing dialogue is repeated at most this often
ROAM_ROUNDS = 3     # times a field's exits are reopened while roaming with nothing left to try
PRESS_REACH = 16   # as close as collect.LINE_REACH: Circle only counts while touching the line
TALK_BUDGET = 10.0
TRAVEL_SPEED = 30.0  # field units per second allowed for the walk, well under running speed


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
        self.attempts_by_field = {}
        self.visits = Counter()
        self.last_field = None
        self.context = None
        self.unsure = False
        self.yields = Counter()   # candidate -> dialogues it produced
        self.reopened = Counter() # (field, progress) -> times its exits were reopened
        self.sweeps = 0           # full searches finished without story progress
        self.armed = None         # contact line Cloud has stepped away from and may now re-enter
        self.talking = False

    def _reset(self, key):
        self.key, self.current = key, None
        self.tried = self.attempts_by_field.setdefault(key, set())

    def observe(self, state):
        """Called every tick: dialogue during a talk attempt means it worked."""
        context = (state.get('story_progress'), tuple(state.get('key_items', [])))
        if context != self.context:
            self.context = context
            self.attempts_by_field.clear()
            self.visits.clear()
            self.talked.clear()
            self.key = self.current = self.last_field = None
            self.unsure = False
            self.yields.clear()
            self.reopened.clear()
            self.sweeps = 0
            self.armed = None
        field = state.get('field_id')
        if state.get('mode') in ('field', 'dialog') and field != self.last_field:
            # Remember the edge/trigger just used. Returning to a room does not
            # erase attempts and start the same two-room loop again.
            if self.current:
                self.tried.add(self.current[0])
                self.current = None
            self.visits[field] += 1
            self.last_field = field
        talking = state.get('mode') == 'dialog'
        if self.current and talking and self.current[0][0] == 'talk':
            self.tried.add(self.current[0])
            if self.everything:
                self.talked.add((state.get('field_id'), self.current[0][1]))
            self.current = None
        elif self.current and talking and not self.talking and self.unsure:
            # It did something. The same door can hold several conversations
            # (jail cell: one per companion), so it stays a candidate for a while.
            cid = self.current[0]
            self.yields[cid] += 1
            if self.yields[cid] >= MAX_YIELDS:
                self.tried.add(cid)
            self.current = self.armed = None
        self.talking = talking

    @staticmethod
    def hot(state, trigger):
        """The trigger's own script moves the story on at the current progress."""
        gates = trigger.get('gates') or []
        return bool(trigger.get('advances') and (not gates or state.get('story_progress') in gates))

    def candidates(self, state, extra_talks=False):
        triggers = state.get('field_triggers') or {}
        actors = {a.get('entity'): a for a in state.get('actors', []) if a.get('talkable')}
        position = state['player']['position']
        found = []
        def reachable(target):
            try:
                steering(state, target)
                return True
            except NoPath:
                return False
        known_talks = {t['entity'] for t in triggers.get('talks', [])}
        for actor in (actors.values() if self.unsure and not extra_talks else ()):
            if actor.get('entity') in known_talks or not actor['visible']:
                continue
            target = actor_near(state, actor, actor.get('entity_name'))
            if not reachable(target):
                # On a bed, behind a counter: stand on the nearest floor and face it.
                target = beside_actor(state, actor)
            if target:
                found.append((3, actor['distance'], ('poke', actor.get('entity')), target))
        for line in triggers.get('lines', []):
            door = not any(i in (3, 4) for i in line['scripts']) and not line['ok']
            hot = self.hot(state, line)
            if (self.unsure or line.get('keys')) and not extra_talks:
                # A walk-over script that waits for a button is a "press Circle here" spot
                # (live 2026-10-12, eleout lin0: the glass elevator's controls).
                a, b = line['line']
                press = dict(field_id=state['field_id'], position=[(a[k]+b[k])/2 for k in range(3)], line=line['line'],
                             inspect=True, ok_line=True, radius=PRESS_REACH)
                if not line['ok'] and reachable(press):
                    found.append((-2 if hot and line.get('keys') else 0 if line.get('keys') else 3,
                                  math.dist(press['position'][:2], position[:2]), ('press', line['entity']), press))
            if door and not self.everything and not self.unsure and not hot:
                continue
            tier = 3 if line['asks'] else -1 if hot else 0 if line['jumps'] else 2 if door else 1
            point = _walkable(state, line['line'])
            if point is None:
                continue
            kind = 'ok_line' if line['ok'] else 'line'
            if self.unsure and not reachable(dict(field_id=state['field_id'], position=point, radius=0)):
                continue
            found.append((tier, math.dist(point[:2], position[:2]), (kind, line['entity']),
                          dict(field_id=state['field_id'], position=point, line=line['line'], radius=0, gateway=-1)
                          if kind == 'line' else
                          dict(field_id=state['field_id'], position=point, line=line['line'], inspect=True, ok_line=True,
                               radius=state['player'].get('collision_radius', 0)+8)))
        for talk in triggers.get('talks', []):
            actor = actors.get(talk['entity'])
            if actor is None:
                continue
            tier = 3 if talk['asks'] else -1 if self.hot(state, talk) else 2
            found.append((tier, actor['distance'], ('talk', talk['entity']),
                          dict(field_id=state['field_id'], position=actor['position'], triangle=actor['triangle'],
                               radius=max(48, actor['collision_radius']+state['player']['collision_radius']+10),
                               actor=actor['entity_name'], entity=talk['entity'])))
        # Ordinary exits are read directly from the loaded field geometry.
        # Try local story interactions first, then prefer unvisited destinations.
        for gateway in (() if extra_talks else state.get('gateways', [])):
            point = [sum(p[k] for p in gateway['line']) / 2 for k in range(3)]
            target = dict(field_id=state['field_id'], position=point, radius=0,
                          gateway=gateway['id'], line=gateway['line'],
                          destination=gateway['destination'])
            try:
                steering(state, target)
            except NoPath:
                continue
            found.append((4 + self.visits[gateway['destination']],
                          math.dist(point[:2], position[:2]), ('gateway', gateway['id']), target))
        return sorted(found, key=lambda e: (e[0], e[1], e[2]))

    def target(self, state):
        self.observe(state)
        key = (state['field_id'], state['story_progress'])
        if key != self.key:
            self._reset(key)
        now = self.clock()
        if self.current and now-self.current[1] > self.current[2]:
            self.tried.add(self.current[0])
            self.current = None
        options = [c for c in self.candidates(state) if c[2] not in self.tried]
        if not options and not self.unsure:
            # Nothing obvious is left: widen to every line, door and model here.
            self.unsure = True
            options = [c for c in self.candidates(state) if c[2] not in self.tried]
        if not options and self.reopened[key] < ROAM_ROUNDS:
            # A dead-end room must not park the run: its exits become usable
            # again, least-visited destination first, so the search moves on
            # through the building (live 2026-10-11, blin673b). Bounded per
            # field and story progress.
            self.reopened[key] += 1
            self.tried -= {cid for cid in self.tried if cid[0] == 'gateway'}
            options = [c for c in self.candidates(state) if c[2] not in self.tried]
        if not options and self.candidates(state):
            # Never park: forget every attempt (all fields) and search again.
            # The answer may need a second visit, another order or a room
            # reached through a trigger that was only tried once.
            self.sweeps += 1
            self.attempts_by_field.clear()
            self.visits.clear()
            self.yields.clear()
            self.reopened.clear()
            self._reset(key)
            options = self.candidates(state)
        if not options:
            return None, 'Explored every story trigger here without progress; take over or add a route step.'
        tier, distance, cid, target = options[0] if not self.current else next(
            (c for c in options if c[2] == self.current[0]), options[0])
        if not self.current or self.current[0] != cid:
            # Time to get there comes on top: a door across a lobby is not a
            # failed attempt after 12 s (live 2026-10-12, blin1 LINEO, 3400 units).
            self.current = (cid, now, (TALK_BUDGET if cid[0] == 'talk' else LINE_BUDGET)+distance/TRAVEL_SPEED)
            self.armed = None
        if cid[0] == 'line' and self.unsure and self.armed != cid:
            if segment_distance(state['player']['position'], *target['line']) >= REENTER:
                self.armed = cid
            else:
                away = self.away(state, target['line'])
                if away is None:
                    self.armed = cid
                else:
                    return (dict(field_id=state['field_id'], position=away, radius=14, explore=cid),
                            'Exploring (no route step): Step away from a trigger line to walk onto it again.')
        label = {'line': 'Walk through a story trigger', 'ok_line': 'Check a story trigger',
                 'press': 'Stand on a trigger line and press Circle', 'poke': f'Check {target.get("actor") or target.get("actor_name")}',
                 'talk': f'Talk to {target.get("actor")}',
                 'gateway': f'Explore exit to field {target.get("destination")}'}[cid[0]]
        again = f' Search {self.sweeps+1}.' if self.sweeps else ''
        return dict(target, explore=cid), f'Exploring (no route step): {label}.{again}'

    @staticmethod
    def floor_points(state):
        return [[sum(v[k] for v in t['vertices'])/3 for k in range(3)]
                for t in state['navigation']['mesh'] if not t['locked']]

    def away(self, state, line):
        """Nearest reachable floor point at least REENTER from the line."""
        position = state['player']['position']
        points = [p for p in self.floor_points(state) if segment_distance(p, *line) >= REENTER+10
                  and abs(p[2]-position[2]) < 32]
        for point in sorted(points, key=lambda p: math.dist(p[:2], position[:2]))[:8]:
            try:
                steering(state, dict(field_id=state['field_id'], position=point, radius=14))
                return point
            except NoPath:
                continue
        return None

    def give_up(self, cid):
        """The loop saw no effect from this candidate: move on now, not at its budget."""
        self.tried.add(tuple(cid))
        if self.current and self.current[0] == tuple(cid):
            self.current = None

    def talk_first(self, state):
        """--explore: talk to each person once per field before following the route."""
        if not self.everything or state['mode'] != 'field':
            return None, None
        for tier, _, cid, target in self.candidates(state, extra_talks=True):
            if cid[0] == 'talk' and tier < 3 and (state['field_id'], cid[1]) not in self.talked:
                if self.current and self.current[0] == cid and self.clock()-self.current[1] > TALK_BUDGET:
                    self.talked.add((state['field_id'], cid[1]))
                    self.current = None
                    continue
                if not self.current or self.current[0] != cid:
                    self.current = (cid, self.clock(), TALK_BUDGET)
                return dict(target, explore=cid), f'Explore mode: talk to {target.get("actor")}.'
        return None, None


def with_exploration(explorer, state, target, reason, unsure=False):
    if explorer is None or state['mode'] != 'field' or state.get('movement_locked'):
        return target, reason
    explorer.unsure = explorer.unsure or unsure
    if target is None and (reason or '').startswith('No verified story objective'):
        return explorer.target(state)
    if target is not None and not target.get('explore'):
        talk, why = explorer.talk_first(state)
        if talk:
            return talk, why
    return target, reason
