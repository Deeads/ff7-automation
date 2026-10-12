"""Read an optional local field-story catalogue against verified live PSX state.

The catalogue is reference data, not executable instructions. Geometry/entities
must match the loaded field. Unknown condition/navigation semantics fail closed.
The bundled handcrafted route retains priority. This is broader field guidance,
not a replacement for world travel, minigames, or verified dialogue answers.
"""
import json
import math
from pathlib import Path
import time

from navigation import NoPath, steering
from route import actor_near, bank_byte, gateway_near, RouteError

DEFAULT_PATH = Path(__file__).resolve().parent / 'reference/field_story_events.json'
CONDITION_KEYS = {'bank', 'address', 'mask', 'value', 'anyBitSet', 'minimumSetBits',
                  'maximumSetBits', 'minimumValue', 'maximumValue', 'partyMemberId', 'requirePartyMember'}
ROW_KEYS = {'fieldId', 'kind', 'label', 'entityId', 'x', 'y', 'z', 'targetGameMoment',
            'minimumGameMoment', 'maximumGameMoment', 'priority', 'sourceFieldName',
            'sourceEntityName', 'sourceScriptType', 'completesOnArrival', 'triggerLine',
            'requiredCondition', 'requiredConditions', 'completedCondition',
            'requiredEnabledLineEntityId', 'requiredPlayerTriangles', 'excludedPlayerTriangles',
            'completionPlayerTriangles', 'usesPlayerCollisionRadius', 'usesContactRange',
            'routeDetour', 'routeDetours', 'manualNavigationGuidance', 'usesHiddenTalkTarget'}


def condition_matches(state, condition):
    if not condition:
        return True
    if not isinstance(condition, dict) or set(condition) - CONDITION_KEYS:
        raise RouteError('Unsupported catalogue condition.')
    if condition.get('partyMemberId') is not None:
        present = condition['partyMemberId'] in [p['id'] for p in state.get('party', [])]
        return present == condition.get('requirePartyMember', False)
    mask = condition.get('mask', 0)
    if not isinstance(mask, int) or not 0 <= mask <= 255:
        raise RouteError('Invalid catalogue condition mask.')
    if not mask:
        return not any(condition.get(k) is not None for k in
                       ('minimumValue', 'maximumValue', 'minimumSetBits', 'maximumSetBits'))
    value = bank_byte(state, condition['bank'], condition['address']) & mask
    if any(condition.get(k) is not None for k in ('minimumValue', 'maximumValue')):
        return condition.get('minimumValue', 0) <= value <= condition.get('maximumValue', 255)
    if any(condition.get(k) is not None for k in ('minimumSetBits', 'maximumSetBits')):
        return condition.get('minimumSetBits', 0) <= value.bit_count() <= condition.get('maximumSetBits', 8)
    return bool(value) if condition.get('anyBitSet') else value == condition.get('value', 0)


def applies(state, row):
    if not isinstance(row, dict) or set(row) - ROW_KEYS:
        return False
    if row['fieldId'] != state['field_id'] or row['sourceFieldName'] != state.get('field_name'):
        return False
    progress = state['story_progress']
    lo, hi = row.get('minimumGameMoment', -1), row.get('maximumGameMoment', -1)
    if lo >= 0 and progress < lo or hi >= 0 and progress > hi:
        return False
    if row.get('targetGameMoment', -1) >= 0 and progress >= row['targetGameMoment']:
        return False
    if any(row.get(k) for k in ('routeDetour', 'routeDetours', 'manualNavigationGuidance',
                                'usesHiddenTalkTarget', 'usesContactRange')):
        return False
    line = row.get('requiredEnabledLineEntityId')
    if line is not None and line not in state.get('enabled_lines', []):
        return False  # Static script presence cannot establish runtime enablement.
    triangle = state['player']['triangle']
    if row.get('requiredPlayerTriangles') and triangle not in row['requiredPlayerTriangles']:
        return False
    if triangle in row.get('excludedPlayerTriangles', []) or triangle in row.get('completionPlayerTriangles', []):
        return False
    if row.get('completedCondition') and condition_matches(state, row['completedCondition']):
        return False
    return all(condition_matches(state, c) for c in
               [row.get('requiredCondition'), *row.get('requiredConditions', [])])


def target_for(state, row):
    kind, script = row['kind'], row.get('sourceScriptType', '')
    if kind == 'Model' and script in ('Talk', 'Main'):
        actor = next((a for a in state.get('actors', [])
                      if a.get('entity') == row['entityId'] and
                      a.get('entity_name') == row['sourceEntityName'] and
                      a.get('visible') and a.get('talkable')), None)
        if actor is not None:
            return actor_near(state, actor, actor['entity_name'])
    if kind == 'Location' and script.startswith('Gateway'):
        target = gateway_near(state, [row['x'], row['y'], row['z']])
        if math.dist(target['position'], [row['x'], row['y'], row['z']]) > 64:
            return None
        if row.get('triggerLine'):
            line = row['triggerLine']
            expected = [[line[f'{end}{axis}'] for axis in 'XYZ'] for end in ('start', 'end')]
            if target['line'] not in (expected, expected[::-1]):
                return None
        return target
    if kind == 'Location' and script in ('Move', 'Go', 'Move/Go', 'Go 1x', '[OK]'):
        line = row.get('triggerLine')
        if not line:
            return None
        expected = [[line[f'{end}{axis}'] for axis in 'XYZ'] for end in ('start', 'end')]
        native = next((l for l in (state.get('field_triggers') or {}).get('lines', [])
                       if l['name'] == row['sourceEntityName'] and l['line'] == expected), None)
        if native is None:
            return None
        from explore import _walkable
        position = _walkable(state, expected)
        if position is None:
            return None
        if script == '[OK]':
            if not native['ok']:
                return None
            return dict(field_id=state['field_id'], position=position, line=expected,
                        inspect=True, ok_line=True, radius=state['player'].get('collision_radius', 0) + 8)
        return dict(field_id=state['field_id'], position=position, line=expected, radius=0, gateway=-1)
    return None


class StoryCatalog:
    def __init__(self, path=DEFAULT_PATH, clock=time.monotonic):
        self.path, self.clock = Path(path), clock
        self.stamp = None
        self.rows = {}
        self.error = None
        self.context = None
        self.attempted = set()
        self.current = None

    def _load(self):
        try:
            stamp = self.path.stat().st_mtime_ns
            if stamp == self.stamp:
                return
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('schemaVersion') != 1 or not isinstance(data.get('definitions'), list):
                raise ValueError('Unsupported story catalogue schema.')
            rows = {}
            for index, row in enumerate(data['definitions']):
                if isinstance(row, dict) and isinstance(row.get('fieldId'), int):
                    rows.setdefault(row['fieldId'], []).append((index, row))
            self.rows, self.stamp, self.error = rows, stamp, None
            self.attempted.clear()
            self.current = None
        except FileNotFoundError:
            self.rows = {}
        except (OSError, ValueError, TypeError) as exc:
            self.rows = {}
            self.error = str(exc)

    def target(self, state):
        if state['mode'] != 'field' or state.get('movement_locked') or state.get('fading'):
            return None, None
        self._load()
        context = (state['story_progress'], tuple(state.get('key_items', [])), state.get('connection_epoch'))
        if context != self.context:
            self.context, self.current = context, None
            self.attempted.clear()
        if self.current and (self.current[0] != state['field_id'] or self.clock() - self.current[2] >= 15):
            self.attempted.add(self.current[:2])
            self.current = None
        candidates = []
        for index, row in self.rows.get(state['field_id'], []):
            if (state['field_id'], index) in self.attempted:
                continue
            try:
                if not applies(state, row):
                    continue
                target = target_for(state, row)
                if target is None:
                    continue
                steering(state, target)
                distance = math.dist(target['position'][:2], state['player']['position'][:2])
                candidates.append((row.get('priority', 100), distance, index, target, row['label']))
            except (KeyError, ValueError, TypeError, IndexError, NoPath):
                continue  # Missing variables or mismatched geometry cannot authorize input.
        if not candidates:
            self.current = None
            return None, None
        candidates.sort(key=lambda c: c[:3])
        chosen = next((c for c in candidates if self.current and c[2] == self.current[1]), candidates[0])
        _, _, index, target, label = chosen
        if self.current is None or self.current[1] != index:
            self.current = (state['field_id'], index, self.clock())
        return dict(target, catalog=index, route_source='catalogue; not live-verified'), f'Catalogue: {label}'
