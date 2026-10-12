"""Data-driven story route: routes/*.json -> the current field objective.

A step applies when the field, the progress range and every `when` condition
match and its `until` (verification) condition does not hold yet. Everything
is read from game state, so loading an older save resyncs. No matching step
means `blocked`: the route never guesses.
"""
import json
import math
from pathlib import Path

from navigation import NoPath, locate, steering, segment_distance

ROUTES = Path(__file__).resolve().parent/'routes'
EXIT_TYPES = ('gateway', 'gateway_at', 'line')
TARGET_TYPES = EXIT_TYPES+('actor', 'actor_name', 'inspect', 'ok_line', 'hold', 'puzzle', 'walk_through')
# Sticky ping-pong state for walk_through steps: key -> [goal index, passes].
_passes = {}


def reset_runtime():
    """A reloaded save must not inherit walk-through attempts from the old one."""
    _passes.clear()


class RouteError(ValueError):
    pass


def load(paths=None):
    steps, choices = [], []
    for path in paths or sorted(ROUTES.glob('*.json')):
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        flags = data.get('flags', {})
        resolve = lambda conds: [flags[c] if isinstance(c, str) else c for c in conds or []]
        for step in data['steps']:
            step = dict(step, when=resolve(step.get('when')), until=resolve(step.get('until')))
            kind = step['target']['type']
            if kind not in TARGET_TYPES:
                raise RouteError(f'{step["id"]}: unknown target type {kind}.')
            if not step['until'] and kind not in EXIT_TYPES+('puzzle',):
                raise RouteError(f'{step["id"]}: needs an until/verification condition.')
            if 'progress' not in step:
                raise RouteError(f'{step["id"]}: needs a progress range.')
            steps.append(step)
        for choice in data.get('choices', []):
            choices.append(dict(choice, when=resolve(choice.get('when'))))
    ids = [s['id'] for s in steps]
    if len(ids) != len(set(ids)):
        raise RouteError('Duplicate route step ids.')
    return steps, choices


def bank_byte(state, bank, address):
    banks = state.get('banks') or {}
    raw = banks.get(str(bank))
    if raw is None or not 0 <= address < 256:
        raise RouteError(f'Bank {bank} is not available in this state.')
    return bytes.fromhex(raw)[address]


def holds(state, condition):
    if 'progress_at_least' in condition:
        return state['story_progress'] >= condition['progress_at_least']
    value = bank_byte(state, condition['bank'], condition['address'])
    if 'any' in condition:
        return bool(value & condition['any'])
    return value & condition['mask'] == condition['value']


def applies(state, step):
    fields = step['field'] if isinstance(step['field'], list) else [step['field']]
    low, high = step['progress']
    if step.get('goal'):
        import goals
        if step['goal'] not in goals.active_ids(state):
            return False
    return (state['field_id'] in fields and low <= state['story_progress'] <= high
            and all(holds(state, c) for c in step['when'])
            and not (step['until'] and all(holds(state, c) for c in step['until'])))


def current_step(state, steps):
    return next((step for step in steps if applies(state, step)), None)


def midpoint(line):
    return [sum(p[k] for p in line)/2 for k in range(3)]


def gateway_by_destination(state, destination):
    reachable = []
    for gateway in state['gateways']:
        if gateway['destination'] != destination:
            continue
        target = dict(field_id=state['field_id'], position=midpoint(gateway['line']), radius=0,
                      gateway=gateway['id'], line=gateway['line'])
        try:
            _, path = steering(state, target)
        except NoPath:
            continue
        reachable.append((len(path), gateway['id'], target))
    if not reachable:
        raise NoPath(f'No reachable exit to field {destination}.')
    return min(reachable)[2]


def gateway_near(state, position):
    gateway = min(state['gateways'], key=lambda g: math.dist(midpoint(g['line'])[:2], position[:2]), default=None)
    if not gateway or math.dist(midpoint(gateway['line'])[:2], position[:2]) > 64:
        raise NoPath(f'No field exit near {position}.')
    return dict(field_id=state['field_id'], position=midpoint(gateway['line']), radius=0,
                gateway=gateway['id'], line=gateway['line'])


def actor_near(state, actor, name):
    radius = max(48, actor['collision_radius']+state['player']['collision_radius']+10)
    if not name:
        # Models without a script name (the jail's dead guard) cannot be followed
        # by name: walk beside the spot, face it, press Circle. Live 2026-10-11:
        # treated as a plain waypoint, the bot stood there on "Route target reached".
        return dict(field_id=state['field_id'], position=actor['position'], radius=radius, inspect=True,
                    entity=actor.get('entity'))
    return dict(field_id=state['field_id'], position=actor['position'], triangle=actor['triangle'],
                radius=radius, actor=name, entity=actor.get('entity'))


BESIDE_RANGE = 110   # how near a model on a bed/counter must be to a walkable spot


def beside_actor(state, actor):
    """A model whose own triangle cannot be walked to (Tifa asleep on the jail
    bed, z 63): stand on the nearest reachable floor within reach and face it."""
    position = state['player']['position']
    floor = [[sum(v[k] for v in t['vertices'])/3 for k in range(3)]
             for t in state['navigation']['mesh'] if not t['locked']]
    near = sorted((p for p in floor if math.dist(p[:2], actor['position'][:2]) <= BESIDE_RANGE
                   and abs(p[2]-position[2]) < 32), key=lambda p: math.dist(p[:2], actor['position'][:2]))
    for point in near[:6]:
        target = dict(field_id=state['field_id'], position=point, face=actor['position'], inspect=True, radius=14,
                      entity=actor.get('entity'), actor_name=actor.get('entity_name'))
        try:
            steering(state, target)
            return target
        except NoPath:
            continue
    return None


def reachable_actor(state, actor, name):
    target = actor_near(state, actor, name)
    try:
        steering(state, target)
        return target
    except NoPath:
        return beside_actor(state, actor) or target


def walkable_point(state, line, samples=32):
    mesh = (state.get('navigation') or {}).get('mesh')
    if not mesh:
        return midpoint(line)
    a, b = line
    try:
        locate(mesh, midpoint(line))
        return midpoint(line)
    except NoPath:
        pass
    position = state['player']['position']
    best = None
    for i in range(1, samples):
        point = [a[k]+(b[k]-a[k])*i/samples for k in range(3)]
        try:
            locate(mesh, point)
        except NoPath:
            continue
        distance = math.dist(point[:2], position[:2])
        if best is None or distance < best[0]:
            best = (distance, point)
    if best is None:
        raise NoPath('No part of the trigger line is on the walkmesh.')
    return best[1]


def build_target(state, step):
    spec = step['target']
    if spec.get('fallback'):
        primary = {key: value for key, value in spec.items() if key != 'fallback'}
        try:
            target = build_target(state, dict(step, target=primary))
            steering(state, target)
            return target
        except NoPath:
            target = build_target(state, dict(step, target=spec['fallback']))
            steering(state, target)
            return target
    kind = spec['type']
    field = state['field_id']
    if kind == 'gateway':
        return gateway_by_destination(state, spec['destination'])
    if kind == 'gateway_at':
        return gateway_near(state, spec['position'])
    if kind == 'line':
        # Crossing a script LINE starts the scene; push across like an exit.
        # Script lines often run past the walkable floor (blin67_1's Hojo line
        # has its midpoint off the walkmesh), so aim for the nearest walkable
        # point on the line instead of its midpoint.
        return dict(field_id=field, position=walkable_point(state, spec['line']), radius=0, gateway=-1, line=spec['line'])
    if kind == 'ok_line':
        return dict(field_id=field, position=midpoint(spec['line']), line=spec['line'],
                    radius=state['player'].get('collision_radius', 0)+8, inspect=True, ok_line=True)
    if kind == 'actor':
        matches = [a for a in state['actors'] if a.get('entity') == spec['entity'] and a['talkable']]
        if not matches:
            raise NoPath(f'Entity {spec["entity"]} is not visible/talkable.')
        return reachable_actor(state, matches[0], matches[0]['entity_name'])
    if kind == 'actor_name':
        matches = [a for a in state['actors'] if a['entity_name'] == spec['name'] and a['talkable']]
        if not matches:
            raise NoPath(f'Cannot find the expected character {spec["name"]}.')
        return reachable_actor(state, min(matches, key=lambda a: a['distance']), spec['name'])
    if kind == 'inspect':
        target = dict(field_id=field, position=spec['position'], radius=spec.get('radius', 18), inspect=True)
        for key in ('face', 'triangle'):
            if key in spec:
                target[key] = spec[key]
        return target
    if kind == 'walk_through':
        return walk_through(state, step)
    if kind == 'hold':
        position = state['player']['position']
        return dict(field_id=field, position=list(position), radius=0, hold=spec['direction'])
    raise RouteError(f'Unsupported target type {kind}.')


def walk_through(state, step):
    """Walk back and forth through a LINE whose script runs on movement.

    Some LINE entities (e.g. the blin66_3 vent) run their event from the
    'move' script, not the [OK] script: Circle does nothing, and spawning
    already inside the line's range never counts as entering it. Alternate
    between a point outside the range and one past the line until the
    script takes over (dialogue/movement lock) or max_passes is reached.
    """
    spec = step['target']
    points = spec['points']
    position = state['player']['position']
    key = (step['id'], state['field_id'])
    entry = _passes.get(key)
    if entry is None:
        far = max(range(2), key=lambda i: math.dist(position[:2], points[i][:2]))
        entry = _passes[key] = [far, 0]
    radius = spec.get('radius', 14)
    if math.dist(position[:2], points[entry[0]][:2]) <= radius:
        entry[0] = 1-entry[0]
        entry[1] += 1
    if entry[1] >= spec.get('max_passes', 8):
        raise NoPath(f'Walked through the trigger {entry[1]} times without the event starting.')
    return dict(field_id=state['field_id'], position=points[entry[0]], radius=radius, walk_through=True)


def line_distance_to(state, target):
    a, b = target['line']
    return segment_distance(state['player']['position'], a, b)


def choice_answer(state, choices, text):
    for choice in choices:
        if choice['field'] != state['field_id']:
            continue
        if choice.get('field_name') and choice['field_name'] != state.get('field_name'):
            continue
        if 'progress' in choice and not choice['progress'][0] <= state['story_progress'] <= choice['progress'][1]:
            continue
        if not all(s in text for s in choice.get('contains', [])):
            continue
        if not all(s in text.lower() for s in choice.get('contains_lower', [])):
            continue
        try:
            if not all(holds(state, c) for c in choice['when']):
                continue
        except (RouteError, KeyError):
            continue
        answer = choice['answer']
        if isinstance(answer, str):
            return answer
        if 'puzzle' in answer:
            return state['puzzles'][answer['puzzle']]
        if 'one_of' in answer:
            visible = [line.strip(' ?"') for line in text.splitlines()]
            return next((a for a in answer['one_of'] if a in visible), None)
        if 'escort_lowest_health' in answer:
            visible = [line.strip(' ?"') for line in text.splitlines()]
            candidates = []
            for option in answer['escort_lowest_health']:
                member = next((p for p in state.get('party', []) if p['id'] == option['party_id']), None)
                if not member or member.get('max_hp', 0) <= 0 or not 0 <= member.get('hp', -1) <= member['max_hp']:
                    return None
                label = member['name'] + option['suffix']
                if visible.count(label) != 1:
                    return None
                candidates.append((member['hp'] / member['max_hp'], len(candidates), label))
            return min(candidates)[2] if candidates else None
    return None
