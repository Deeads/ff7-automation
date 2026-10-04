"""Save-driven story objectives. All interaction is ordinary controller input."""
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
        wanted = route.choice_answer(state, route_table()[1], text)
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


_table = None


def route_table():
    global _table
    if _table is None:
        _table = route.load()
    return _table


def objective(state):
    """Current step of the data-driven route (routes/*.json); resyncs from save flags."""
    if state['mode'] not in ('field', 'dialog', 'menu'):
        return None, 'Waiting for a supported gameplay state.'
    if state['mode'] != 'field':
        return None, 'Resolve the current dialogue or menu.'
    step = route.current_step(state, route_table()[0])
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
