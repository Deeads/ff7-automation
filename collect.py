"""Optional objective layer: open reachable chests / pickups before leaving a field.

Targets come from `state['field_checks']` (the field's own scripts). Success
is only ever the game's own flag flipping; a treasure whose flag is still
clear after `max_tries` interactions is skipped and logged, never retried
forever.
"""
from collections import Counter

from explore import _walkable
from navigation import steering, NoPath


LINE_REACH = 16


def flag_text(flag):
    bank, address, bit = flag
    return f'bank {bank}[{address}] bit {bit}'


class Collector:
    def __init__(self, max_tries=2):
        self.max_tries = max_tries
        self.attempts = Counter()
        self.skipped = {}
        self.events = []   # drained by the loop into decisions.jsonl

    @staticmethod
    def key(state, treasure):
        return state['field_id'], treasure['entity'], tuple(treasure['flag'])

    def _settled(self, state):
        # Chest scripts set their flag mid-animation; never judge a failed
        # attempt until the script and its dialogue have finished.
        return state['mode'] == 'field' and not state.get('movement_locked') and not state.get('dialogs') and not (state.get('player') or {}).get('ladder')

    def candidates(self, state):
        if (state.get('player') or {}).get('ladder'):
            return []  # never detour for a chest while the game has Cloud on a ladder/duct
        checks = state.get('field_checks') or {}
        actors = {a['entity']: a for a in state.get('actors', []) if 'entity' in a}
        lines = {l['entity']: l for l in (state.get('field_triggers') or {}).get('lines', [])}
        player = state['player']
        found = []
        for treasure in checks.get('treasures', []):
            key = self.key(state, treasure)
            if treasure['taken'] or key in self.skipped:
                continue
            if self.attempts[key] >= self.max_tries:
                if self._settled(state):
                    reason = f'Flag {flag_text(treasure["flag"])} did not flip after {self.attempts[key]} tries.'
                    self.skipped[key] = reason
                    self.events.append(dict(event='collect_skipped', field_id=state['field_id'],
                                            entity=treasure['entity'], name=treasure['name'], reason=reason))
                continue
            actor = actors.get(treasure['entity'])
            info = dict(name=treasure['name'], flag=treasure['flag'], grants=treasure['grants'])
            if treasure['entity'] in lines:
                # Lockers and shelves are line triggers whose script reads Circle
                # while Cloud touches the line. Live on blin64 RLINA: 13.7 units
                # from the middle worked, 42 from an end did not.
                line = lines[treasure['entity']]['line']
                point = _walkable(dict(state, player=dict(player, position=[(line[0][k]+line[1][k])/2 for k in range(3)])), line)
                if point is None:
                    continue
                target = dict(field_id=state['field_id'], position=point, line=line, inspect=True, ok_line=True,
                              radius=LINE_REACH, entity=treasure['entity'], collect=info)
            elif not actor or not actor['talkable'] or not actor['visible']:
                continue
            else:
                target = dict(field_id=state['field_id'], position=actor['position'], triangle=actor['triangle'],
                              radius=max(48, actor['collision_radius']+player.get('collision_radius', 0)+10),
                              actor=actor['entity_name'], entity=treasure['entity'], collect=info)
            try:
                _, path = steering(state, target)
            except NoPath:
                continue
            found.append((len(path), treasure['entity'], target))
        return [target for _, _, target in sorted(found, key=lambda item: item[:2])]

    def target(self, state):
        options = self.candidates(state)
        if not options:
            return None, None
        target = options[0]
        info = target['collect']
        return target, f'Collect {info["name"]} ({flag_text(info["flag"])}) before leaving the field.'

    def attempted(self, state, target):
        info = target.get('collect')
        if info:
            key = (state['field_id'], target['entity'], tuple(info['flag']))
            self.attempts[key] += 1
            self.events.append(dict(event='collect_attempt', field_id=state['field_id'], entity=target['entity'],
                                    name=info['name'], attempt=self.attempts[key]))

    def drain(self):
        events, self.events = self.events, []
        return events


def with_collection(collector, state, target, reason):
    """Insert a chest detour only where the story is about to leave the field."""
    # Script LINE triggers (gateway -1) start scenes; only real exits get a detour.
    if collector is None or state['mode'] != 'field' or not target:
        return target, reason
    if target.get('gateway') is None or target['gateway'] < 0:
        return target, reason
    detour, why = collector.target(state)
    return (detour, why) if detour else (target, reason)
