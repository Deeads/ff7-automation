"""Fetch a Phoenix Down from a known, untaken pickup when someone is KO and none is in stock.

Sources are the game's own field scripts read from the user's disc image
(field_disc + field_script.scan): a pickup that grants the item and whose
"taken" flag is still clear in the live save. Field ids come from the local
story catalogue and from fields seen live. Routes follow each field's gateway
table only; scripted doors, elevators, shops and world travel are not used,
so "none reachable" is a normal answer, not an error.
"""
from collections import deque
import json
from pathlib import Path
import struct
import time

from battle_strategy import PHOENIX_DOWN
from collect import Collector
from discs import DiscLibrary
from field_disc import Disc, lzss
from field_script import scan, ScriptError, parse_section, script_ranges, instructions
from navigation import NoPath
from route import bank_byte, gateway_by_destination, RouteError
from story_catalog import DEFAULT_PATH

INDEX = 'field-index.json'
INDEX_VERSION = 2
RESTORE_OPS = (0x3C, 0x3D, 0x3E, 0x3F)   # HMPMAX1-3 / MHMMX: full HP/MP restore (reference/field.py)
SETTLE = 3.0       # seconds in a field before its pickup counts as unobtainable
BUDGET = 600.0     # seconds for one search
MAX_HOPS = 8


def build_index(cue):
    """{field name: exits (field ids) and item pickups} for every field on the disc."""
    disc, fields = Disc(cue), {}
    try:
        for file, (lba, size, folder) in disc.listing('FIELD').items():
            if folder or not file.endswith('.DAT'):
                continue
            try:
                raw = disc.read(lba, size)
                data = lzss(raw[4:4+struct.unpack_from('<I', raw)[0]])
                pointers = struct.unpack_from('<7I', data)
                base = pointers[0]-28
                found = scan(data[28:pointers[1]-base])
                triggers = data[pointers[4]-base:pointers[5]-base]
                exits = {struct.unpack_from('<H', triggers, 56+i*24+18)[0] for i in range(12)}
                section = data[28:pointers[1]-base]
                parsed = parse_section(section)
                rests = sorted({parsed['names'][entity] for entity, index, start, end in script_ranges(parsed) if index
                                and any(op in RESTORE_OPS for _, op, _ in instructions(section, start, end))})
                # Encounter section: two 24-byte tables, each enabled byte + rate byte first.
                table = data[pointers[5]-base:pointers[5]-base+2]
                encounters = table[1] if table[0] else 0
            except (ScriptError, struct.error, IndexError):
                continue  # not a field file, or a script the scanner does not cover
            pickups = [dict(name=t['name'], flag=t['flag'],
                            items=[g['id'] for g in t['grants'] if g['kind'] == 'item' and g['literal']])
                       for t in found['treasures']]
            fields[triggers[:9].split(b'\0')[0].decode('ascii', errors='replace')] = dict(
                exits=sorted(exits-{32767}), pickups=[p for p in pickups if p['items']],
                encounters=encounters, rests=rests)
    finally:
        disc.close()
    return fields


def load_index(root):
    """Cached per disc image; raises OSError/ValueError when no disc is available."""
    cue = next((d['path'] for d in DiscLibrary(root).inventory() if d['available']), None)
    if cue is None:
        raise ValueError('No disc image is configured, so pickups in other fields are unknown.')
    stamp = [cue, Path(cue).stat().st_mtime_ns, INDEX_VERSION]
    path = Path(root)/INDEX
    try:
        cached = json.loads(path.read_text(encoding='utf-8'))
        if cached.get('disc') == stamp:
            return cached['fields']
    except (OSError, ValueError):
        pass
    fields = build_index(cue)
    path.write_text(json.dumps(dict(disc=stamp, fields=fields)), encoding='utf-8')
    return fields


def field_path(state, index, names, dead_exits, goal):
    """Field ids from the current field to the nearest other field where goal(id) holds."""
    here = state['field_id']
    exits = lambda field: ({g['destination'] for g in state.get('gateways', [])} if field == here
                           else set(index.get(names.get(field, ''), {}).get('exits', [])))
    queue, seen = deque([[here]]), {here}
    while queue:
        path = queue.popleft()
        if path[-1] != here and goal(path[-1]):
            return path
        if len(path) > MAX_HOPS:
            continue
        for following in sorted(exits(path[-1])):
            if following not in seen and (path[-1], following) not in dead_exits:
                seen.add(following)
                queue.append(path+[following])
    return None


def catalogue_names(path=DEFAULT_PATH):
    try:
        rows = json.loads(Path(path).read_text(encoding='utf-8'))['definitions']
        return {row['fieldId']: row['sourceFieldName'] for row in rows}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def wanted(state):
    """Item id worth fetching, or None."""
    party = state.get('party', [])
    stock = {int(k): v for k, v in state.get('items', {}).items()}
    if any(p.get('hp', 0) <= 0 for p in party) and any(p.get('hp', 0) > 0 for p in party) and not stock.get(PHOENIX_DOWN):
        return PHOENIX_DOWN
    return None


class Supplies:
    def __init__(self, root, index=None, names=None, clock=time.monotonic):
        self.root, self.clock = root, clock
        self.index, self.names = index, names
        self.reset()

    def reset(self):
        self.collector = Collector()
        self.failed = None
        self.started = None
        self.active = False
        self.dead_exits = set()     # (field id, destination id) with no walkable gateway
        self.exhausted = set()      # field ids whose pickup could not be reached or opened
        self.arrived = None         # (field id, time first seen here)

    def searching(self, state):
        """True while KO members should not stop the run: a fetch is in progress."""
        if state is not None:
            self.active = wanted(state) is not None and self.failed is None
        return self.active

    def fail(self, reason):
        self.failed = reason
        return None, reason

    def source(self, state, field):
        """An untaken Phoenix Down pickup recorded for `field`, by the live save flags."""
        entry = self.index.get(self.names.get(field, ''), {})
        for pickup in entry.get('pickups', []):
            try:
                taken = bank_byte(state, pickup['flag'][0], pickup['flag'][1]) >> pickup['flag'][2] & 1
            except RouteError:
                continue
            if PHOENIX_DOWN in pickup['items'] and not taken:
                return pickup
        return None

    def path(self, state):
        """Field ids from here to the nearest source, by gateway tables."""
        return field_path(state, self.index, self.names, self.dead_exits,
                          lambda field: field not in self.exhausted and self.source(state, field))

    def target(self, state):
        """(target, reason) while fetching; (None, None) when not involved;
        (None, reason) to wait here or after giving up (`failed` is then set)."""
        if state.get('mode') != 'field' or state.get('movement_locked') or state.get('fading'):
            return None, None
        if wanted(state) is None:
            if self.started is not None:
                self.reset()
            return None, None
        if self.failed:
            return None, self.failed
        now = self.clock()
        if self.started is None:
            self.started = now
        if now-self.started > BUDGET:
            return self.fail('Phoenix Down search ran out of time.')
        here = state['field_id']
        if self.arrived is None or self.arrived[0] != here:
            self.arrived = (here, now)
        local = [t for t in self.collector.candidates(state)
                 if any(g['kind'] == 'item' and g['id'] == PHOENIX_DOWN for g in t['collect']['grants'])]
        if local:
            return local[0], f'Fetch a Phoenix Down: open {local[0]["collect"]["name"]}.'
        try:
            if self.index is None:
                self.index = load_index(self.root)
            if self.names is None:
                self.names = catalogue_names()
        except (OSError, ValueError) as exc:
            return self.fail(f'Phoenix Down search unavailable: {exc}')
        self.names[here] = state.get('field_name', self.names.get(here))
        if here not in self.exhausted and self.source(state, here):
            # The pickup is recorded here but cannot be approached or opened (yet).
            if now-self.arrived[1] < SETTLE:
                return None, 'Fetch a Phoenix Down: waiting for the pickup to become reachable.'
            self.exhausted.add(here)
        while True:
            path = self.path(state)
            if path is None:
                return self.fail('No untaken Phoenix Down pickup is reachable through field exits.')
            try:
                target = gateway_by_destination(state, path[1])
            except NoPath:
                self.dead_exits.add((here, path[1]))
                continue
            return dict(target, supply=path[-1]), (
                f'Fetch a Phoenix Down from {self.names[path[-1]]} ({len(path)-1} field{"s" if len(path) > 2 else ""} away).')

    def attempted(self, state, target):
        if target.get('collect'):
            self.collector.attempted(state, target)
