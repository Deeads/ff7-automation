"""Save-driven story objectives. All interaction is ordinary controller input."""
import random
import time
import math
from navigation import locate, steering, segment_distance, NoPath
import route


def gateway_target(state, destination):
    candidates = [g for g in state['gateways'] if g['destination'] == destination]
    reachable = []
    for gateway in candidates:
        point = [sum(v[k] for v in gateway['line'])/2 for k in range(3)]
        target = dict(field_id=state['field_id'], position=point, radius=0, gateway=gateway['id'])
        try:
            direction, path = steering(state, target)
            reachable.append((len(path), gateway['id'], target))
        except NoPath:
            continue
    if not reachable:
        raise NoPath(f'No reachable exit to field {destination}.')
    return min(reachable)[2]


def actor_target(state, name):
    matches = [a for a in state['actors'] if a['entity_name'] == name and a['talkable']]
    if not matches:
        raise NoPath(f'Cannot find the expected character {name}.')
    npc = min(matches, key=lambda a: a['distance'])
    return dict(field_id=state['field_id'], position=npc['position'], triangle=npc['triangle'],
                radius=max(48, npc['collision_radius']+state['player']['collision_radius']+10),
                actor=name)


def dialogue_action(state):
    for dialog in state['dialogs']:
        if not dialog['choice_pointer']:
            continue
        text = dialog['text']
        wanted = state.get('dialogue_answer') or route.choice_answer(state, route_table()[1], text)
        if wanted is None:
            return None, 'No verified answer for this dialogue choice.'
        rows = [i for i, line in enumerate(text.splitlines()) if line.strip(' ?"') == wanted]
        if len(rows) != 1:
            return None, 'Expected dialogue answer is not uniquely visible.'
        current = dialog['choice_row']
        if current is None or current < 0:
            return None, 'Dialogue cursor is not ready.'
        if current != rows[0]:
            direction = 'down' if current < rows[0] else 'up'
            return direction, f'Move the cursor {direction} to the verified correct answer.'
        return 'confirm', 'The correct answer is highlighted. Press confirm to accept it.'
    return None, None


class DialogNudge:
    """A message that stays on screen and offers nothing to confirm.

    Prompts such as "End with the (START) button" (live 2026-10-12, blin70_3,
    the Materia exchange before Rufus) never close by themselves. After STALL
    seconds without change the button the text names is pressed; a message
    that names none gets Circle, Cross and Start in turn. Dialogue choices are
    left to the route and ChoiceGuesser.
    """
    STALL, GAP = 6.0, 2.5
    ORDER = ('confirm', 'cancel', 'start')

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.key = None
        self.since = self.last = 0.0
        self.presses = 0

    def action(self, state):
        dialogs = state.get('dialogs') or []
        if (state.get('mode') != 'dialog' or not dialogs or any(d['choice_pointer'] for d in dialogs)
                or any(d['ready'] and not d['auto_close'] for d in dialogs)):
            self.key = None
            return None
        now = self.clock()
        key = (state.get('field_id'), tuple(d['text'] for d in dialogs))
        if key != self.key:
            self.key, self.since, self.presses = key, now, 0
            return None
        if now-self.since < self.STALL or now-self.last < self.GAP:
            return None
        named = 'start' if any('START' in d['text'].upper() for d in dialogs) else None
        action = named or self.ORDER[self.presses % len(self.ORDER)]
        self.last = now
        self.presses += 1
        return action


class ScreenNudge:
    """A screen the bot cannot read that stays up: back out with Cross.

    Live 2026-10-12, field 236: exploration talked to a shopkeeper and the Buy /
    Sell / Exit menu stayed open for 12 minutes. Cross leaves shop, save and
    other menus and does nothing in a movie. Bounded per screen, so a long
    cutscene or minigame is not pressed through forever.
    """
    STALL, GAP, PRESSES = 20.0, 3.0, 6
    # field_state 12 + module request 6: the highway bike chase (live 2026-10-12,
    # field 103). Left alone, the riders bring Barret and Tifa to 1 HP before
    # Motor Ball. Swinging left (Square) and right (Circle) in turn keeps them off.
    BIKE = (12, 6)
    # field_state 7 + module request 5: "Please make a party of three" (live
    # 2026-10-12, field 170, leaving Midgar). Its cursors are not decoded and a
    # fixed key sequence drifted out of step, so the keys are a seeded random
    # walk over both panes, mostly Circle. The save's party slots are the
    # feedback: once three are in, Cross leaves the screen. Bounded.
    PARTY = (7, 5)
    PARTY_GAP, PARTY_INPUTS = 0.45, 900
    PARTY_KEYS = ('confirm',)*8+('down',)*3+('right',)*3+('up',)*2+('left',)*2+('cancel',)*2

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.key = None
        self.since = self.last = 0.0
        self.presses = 0
        self.swings = 0
        self.queue, self.rounds = [], 0
        self.rng = random.Random(7)

    def party(self, state, now):
        if now-self.last < self.PARTY_GAP:
            return None
        if len(state.get('party_slots') or []) >= 3:
            action = 'cancel'
        elif self.rounds >= self.PARTY_INPUTS:
            return None
        else:
            self.rounds += 1
            action = self.rng.choice(self.PARTY_KEYS)
        self.last = now
        return action

    def action(self, state):
        if state.get('mode') != 'unsupported':
            self.key = None
            self.queue, self.rounds = [], 0
            return None
        kind = (state.get('field_state'), state.get('module_request'))
        if kind == self.BIKE:
            self.swings += 1
            return 'square' if self.swings % 2 else 'confirm'
        if kind == self.PARTY:
            return self.party(state, self.clock())
        now = self.clock()
        key = (state.get('field_id'), state.get('module_request'), state.get('field_state'))
        if key != self.key:
            self.key, self.since, self.presses = key, now, 0
            return None
        if now-self.since < self.STALL or now-self.last < self.GAP or self.presses >= self.PRESSES:
            return None
        self.last = now
        self.presses += 1
        return 'cancel'


class ChoiceGuesser:
    """Dialogue choices with no verified answer: try each option once, top to bottom.

    The cursor opens on the first option, so the options are that row and every
    row below it. A choice that comes back gets its next untried option; when
    all were tried the round starts again. Verified answers always win.
    """
    def __init__(self):
        self.tried = {}        # (field id, dialogue text) -> option texts already chosen
        self.open = None       # (key, option text) for the dialogue currently on screen

    def answer(self, state):
        dialog = next((d for d in state.get('dialogs', []) if d['choice_pointer']), None) if state.get('mode') == 'dialog' else None
        if dialog is None:
            self.open = None      # closed: the same choice coming back gets its next option
            return None
        if state.get('dialogue_answer'):
            return None
        text = dialog['text']
        if route.choice_answer(state, route_table()[1], text) is not None:
            return None
        key = (state['field_id'], text)
        if self.open and self.open[0] == key:
            return self.open[1]
        row = dialog['choice_row']
        lines = [line.strip(' ?"') for line in text.splitlines()]
        if row is None or not 0 <= row < len(lines):
            return None
        options = [line for line in lines[row:] if line and lines.count(line) == 1]
        if not options:
            return None
        tried = self.tried.setdefault(key, [])
        if len(tried) >= len(options):
            tried.clear()
        choice = next(option for option in options if option not in tried)
        tried.append(choice)
        self.open = (key, choice)
        return choice


_table = None
_table_version = None


def route_table():
    global _table, _table_version
    paths = sorted(route.ROUTES.glob('*.json'))
    version = tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in paths)
    if _table is None or version != _table_version:
        _table = route.load(paths)
        _table_version = version
    return _table


def objective(state, skip=()):
    """Current step of the data-driven route (routes/*.json); resyncs from save flags."""
    if state['mode'] not in ('field', 'dialog', 'menu'):
        return None, 'Waiting for a supported gameplay state.'
    if state['mode'] != 'field':
        return None, 'Resolve the current dialogue or menu.'
    # `skip`: step ids that were tried here without any effect (laya_loop).
    step = route.current_step(state, [s for s in route_table()[0] if s['id'] not in skip])
    if step is None:
        return None, f'No verified story objective for field {state["field_id"]}, progress {state["story_progress"]}.'
    if step['target']['type'] == 'puzzle':
        return {'midgar': midgar_model_objective}[step['target']['handler']](state)
    try:
        target = route.build_target(state, step)
    except NoPath as exc:
        return None, f'Blocked at {step["id"]}: {exc}'
    target.update(step=step['id'], route_source=step['source'], goal=step.get('goal'))
    return target, step['label']


# Chest bits and carried parts verified against the owned USA field scripts.
# Coordinates/triangle IDs are native model-slot locations; the live walkmesh
# must also contain them before any movement is allowed.
MODEL_PARTS = [
    ('PARTD',0x40,0x40,[342,-194,-3],70,1,2),
    ('PARTB',0x10,0x20,[294,170,0],33,0,128),
    ('PARTC',0x20,0x08,[-342,89,0],75,1,1),
    ('PARTA',0x08,0x10,[-149,283,0],68,0,64),
    ('PARTE',0x80,0x80,[128,-316,0],26,1,4),
]
# A chest's Talk script only opens once one of these placed-part bits is set
# (bank 3[180], blin65_1 PARTx scripts; reference/field_story_events.json).
CHEST_UNLOCK = dict(PARTD=0, PARTB=0xC0, PARTC=0xA0, PARTA=0x88, PARTE=0x90)


def chest_unlocked(name, placed):
    return not CHEST_UNLOCK[name] or bool(placed & CHEST_UNLOCK[name])


def midgar_model_objective(state):
    field, keys = state['field_id'], state['key_items']
    placed = state['puzzles']['midgar_placed']
    if field==248:
        if keys[4]&2:
            return gateway_target(state,250), 'Climb to floor 66 with Keycard 66.'
        if keys[4]&0xF8:
            return gateway_target(state,249), 'Carry the Midgar part into the model room.'
        if placed==0xF8:
            return actor_target(state,'TAKARA'), 'Open the unlocked Keycard 66 chest.'
        for name,carried,bit,point,triangle,byte,mask in MODEL_PARTS:
            if not placed&bit and not state['puzzles']['shinra_chests'][byte]&mask and chest_unlocked(name,placed):
                return actor_target(state,name), 'Inspect '+name+' and collect the next Midgar part.'
        return None, 'No verified story objective: no unlocked Midgar chest and no part carried.'
    elif field==249:
        for name,carried,bit,point,triangle,byte,mask in MODEL_PARTS:
            if keys[4]&carried and not placed&bit:
                return dict(field_id=249,position=point,triangle=triangle,radius=30,
                            inspect=True,face=[0,0]), 'Inspect the matching model slot and place the carried part.'
        return gateway_target(state,248), 'Return for the next chest or Keycard 66.'
    return None, 'No verified story objective: inconsistent Midgar puzzle flags.'


def hold_direction(state, target):
    """On a live LADER, use the game's own advance key; the route's guess is a fallback."""
    ladder = (state.get('player') or {}).get('ladder') or {}
    return ladder.get('forward') or target['hold']


def target_action(state, target):
    position = state['player']['position']
    distance = math.dist(position[:2], target['position'][:2])
    if target.get('hold'):
        return hold_direction(state, target), 'Advance along the ladder or duct.'
    if target.get('ok_line'):
        # [OK] line triggers fire on Circle while touching the line; no facing test.
        if segment_distance(position, *target['line']) <= target['radius']:
            return 'confirm', 'Interact with the line trigger.'
        direction, _ = steering(state, target)
        return direction, 'Follow the walkmesh path.'
    if target.get('inspect') and distance <= target.get('radius',12) and ('triangle' not in target or state['player']['triangle']==target['triangle']):
        if 'triangle' in target:
            # The model's own script tests Cloud's exact triangle and KEYON.
            # Turning with a movement press can walk off that trigger triangle.
            return 'confirm', 'Interact with the puzzle object.'
        delta = [target.get('face',target['position'])[k]-position[k] for k in range(2)]
        direction = max(state['navigation']['direction_vectors'],key=lambda d:sum(delta[k]*state['navigation']['direction_vectors'][d][k] for k in range(2)))
        desired = state['navigation']['direction_angles'][direction]
        difference = abs((state['player']['facing']-desired+128)%256-128)
        return ('confirm' if difference<=32 else direction), 'Interact with the puzzle object.'
    if target.get('actor') and distance <= target['radius']:
        # Facing matters for TALK checks. A brief direction press faces the NPC
        # before confirming; collision prevents walking into a solid NPC.
        delta = [target['position'][i]-position[i] for i in range(2)]
        direction = max(state['navigation']['direction_vectors'], key=lambda d:
            sum(delta[k]*state['navigation']['direction_vectors'][d][k] for k in range(2)))
        desired = state['navigation']['direction_angles'][direction]
        difference = abs((state['player']['facing']-desired+128)%256-128)
        return ('confirm' if difference <= 32 else direction), 'Interact with '+target['actor']+'.'
    if target.get('gateway') is not None and distance < 20:
        a, b = target['line'] if 'line' in target else next(
            g for g in state['gateways'] if g['id'] == target['gateway'])['line']
        normal = [-(b[1]-a[1]), b[0]-a[0]]
        side = sum((position[k]-target['position'][k])*normal[k] for k in range(2))
        if side > 0:
            normal = [-n for n in normal]
        direction = max(state['navigation']['direction_vectors'], key=lambda d:
            sum(normal[k]*state['navigation']['direction_vectors'][d][k] for k in range(2)))
        return direction, 'Cross the field exit.'
    direction, _ = steering(state, target)
    return direction, 'Follow the walkmesh path.'
