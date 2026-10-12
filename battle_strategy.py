"""Combat decisions from decoded battle + save data. Pure: no input here.

IDs verified from the Disc 1 KERNEL.BIN name tables (items section 19,
spells section 18, materia section 23), not guessed:
Potion 0, Hi-Potion 1, Phoenix Down 7; spell Cure 0; materia Restore 53.
"""
from dataclasses import dataclass

POTION, HI_POTION, PHOENIX_DOWN = 0, 1, 7
CURE_SPELL, RESTORE_MATERIA, CURE_MP = 0, 53, 5
HEAL_BELOW = 0.40        # heal any ally under 40% HP
CRITICAL_BELOW = 0.25    # "critical" for the flee rule
IDLE_HEAL = 0.70         # nothing can be targeted: use the turn to top up below this
FINISH_SHARE = 0.20      # enemies at or under this share of their total HP: finish, do not flee


@dataclass(frozen=True)
class Action:
    command: str          # attack | limit | magic | item | flee | wait
    target_side: str = 'enemy'
    target_slot: int = -1
    item: int = -1
    spell: int = -1
    reason: str = ''
    priority: int = 0


def ratio(unit):
    return unit['hp']/unit['max_hp'] if unit['max_hp'] else 0


def choose(battle, actor_slot, party, items, boss=False, reserved=(), allow_switch=True):
    """actor_slot: battle slot (0-2) whose command menu is open.
    party: save records in the same slot order (materia, MP).
    Never flees an inescapable/boss battle."""
    players = battle['players']
    actor = next((p for p in players if p['slot'] == actor_slot), None)
    if actor is None or not actor['alive'] or actor.get('controllable') is False:
        return Action('wait', reason='Active character is not a living party member.')
    member = party[actor_slot] if actor_slot < len(party) else {}
    enemies = [e for e in battle['enemies'] if e['alive'] and e.get('targetable', True)]
    living = [p for p in players if p['alive']]
    dead = [p for p in players if not p['alive']]
    escapable = battle.get('escapable', False) and not boss and not battle.get('boss', False)
    abilities = actor.get('abilities')
    # Legacy fixtures/adapters expose only Restore's guaranteed first spell.
    # A decoded active spell list, even empty, is authoritative.
    if abilities is None:
        abilities = ([dict(id=CURE_SPELL, kind='heal', mp=CURE_MP, potency=5, name='Cure')]
                     if RESTORE_MATERIA in member.get('materia', ()) else [])
    commands = actor.get('commands')
    abilities = [a for a in abilities if (commands is None or 2 in commands) and a.get('available', True) and a['mp'] <= actor['mp']
                 and not actor.get('silenced') and not actor.get('magic_blocked')]
    reserved = set(reserved)

    def magic(ability, target, priority):
        return Action('magic', 'ally', target['slot'], spell=ability['id'],
                      reason=f'{ability.get("name", ability["id"])} on ally {target["slot"]}.', priority=priority)

    def heal(target, priority):
        if ('heal', target['slot']) in reserved:
            return None
        spells = [a for a in abilities if a['kind'] == 'heal' and not target.get('reflect')]
        missing = target['max_hp'] - target['hp']
        if spells:
            # Prefer a known sufficient estimate; otherwise use the strongest
            # affordable tier during emergencies, cheapest for routine recovery.
            sufficient = [a for a in spells if a.get('healing_min', 0) >= target['max_hp']*.6-target['hp']]
            selected = min(sufficient, key=lambda a: a['mp']) if sufficient else (
                max(spells, key=lambda a: a.get('potency', 0)) if priority >= 90
                else min(spells, key=lambda a: a['mp']))
            return magic(selected, target, priority)
        for item in (POTION, HI_POTION) if missing <= 150 else (HI_POTION, POTION):
            if items.get(item, 0) > 0:
                return Action('item', 'ally', target['slot'], item=item,
                              reason=f'Heal ally {target["slot"]} ({target["hp"]}/{target["max_hp"]}).', priority=priority)
        return None

    endangered = sorted((p for p in living if ratio(p) < CRITICAL_BELOW
                         or p['hp'] <= p.get('incoming_damage', 0)), key=ratio)
    for target in endangered:
        action = heal(target, 100)
        if action:
            return action
    rescue = False     # another member can still heal: act meanwhile, do not run
    if endangered and any(('heal', p['slot']) not in reserved for p in endangered):
        for healer in living:
            if healer['slot'] == actor_slot or healer.get('silenced') or healer.get('magic_blocked'):
                continue
            if 2 not in healer.get('commands', [2]):
                continue
            if any(a['kind'] == 'heal' and a.get('available', True) and a['mp'] <= healer['mp']
                   for a in healer.get('abilities', [])) and any(not p.get('reflect') for p in endangered):
                if allow_switch:
                    return Action('switch', priority=100, reason='Switch to a ready healer for emergency recovery.')
                rescue = True

    for target in sorted(dead, key=lambda p: (not p.get('healer', False), p['slot'])):
        if ('revive', target['slot']) in reserved:
            continue
        spells = [a for a in abilities if a['kind'] == 'revive' and not target.get('reflect')]
        if spells:
            return magic(min(spells, key=lambda a: a['mp']), target, 90)
        if items.get(PHOENIX_DOWN, 0) > 0:
            return Action('item', 'ally', target['slot'], item=PHOENIX_DOWN,
                          reason=f'Revive KO ally in slot {target["slot"]} with Phoenix Down.', priority=90)
    for target in living:
        if ('status', target['slot']) in reserved or target.get('reflect'):
            continue
        cures = [a for a in abilities if a['kind'] == 'cure_status' and a.get('removes', 0) & target.get('status', 0)]
        if cures:
            return magic(min(cures, key=lambda a: a['mp']), target, 80)
    hurt = sorted((p for p in living if ratio(p) < HEAL_BELOW), key=ratio)
    if hurt:
        for target in hurt:
            action = heal(target, 70)
            if action:
                return action
    # A KO ally alone is no reason to run (live 2026-10-10: three battles in a
    # row were fled with a healthy survivor, each costing seconds of free hits).
    # Run when a survivor is about to fall or stands alone, unless the enemies
    # are nearly finished.
    critical = any(ratio(p) < CRITICAL_BELOW for p in living) or (dead and len(living) == 1)
    nearly_won = enemies and sum(e['hp'] for e in enemies) <= FINISH_SHARE*sum(e['max_hp'] for e in enemies)
    if critical and escapable and not reserved and not nearly_won and not rescue:
        return Action('flee', reason='Party health critical in an escapable random battle; no heal available.')
    if not enemies:
        # Hidden, retracted or unselectable enemies come back and hit: spend the
        # free turn on recovery instead of idling, never on an ally-side attack.
        for target in sorted((p for p in living if ratio(p) < IDLE_HEAL), key=ratio):
            action = heal(target, 60)
            if action:
                return action
        return Action('wait', reason='No targetable enemy (hidden, retracted or helper parts only).')
    weakest = min(enemies, key=lambda e: (e['hp'], e['slot']))
    # Only a verified offensive Limit may inherit enemy targeting. Legacy
    # callers have no active-spell metadata and retain their original behavior.
    if actor.get('limit_ready') and (actor.get('limit_kind') == 'damage' or 'abilities' not in actor):
        return Action('limit', 'enemy', weakest['slot'], reason='Limit Break is ready.')
    reserve_mp = min((a['mp'] for a in abilities if a['kind'] == 'heal'), default=0)
    offensive = []
    for ability in abilities:
        if ability['kind'] != 'damage' or actor['mp']-ability['mp'] < reserve_mp:
            continue
        for enemy in enemies:
            if enemy.get('reflect') or enemy.get('magic_immune') or enemy.get('counter_magic'):
                continue
            multiplier = elemental_multiplier(ability.get('elements', 0), enemy)
            attack_available = commands is None or 1 in commands
            if multiplier is None or multiplier <= 0 or (multiplier <= 1 and attack_available):
                continue
            score = multiplier*ability.get('potency', 1)/max(1, ability['mp'])
            offensive.append((score, -enemy['hp'], -ability['mp'], ability, enemy))
    if offensive:
        _, _, _, ability, enemy = max(offensive, key=lambda row: row[:3])
        return Action('magic', 'enemy', enemy['slot'], spell=ability['id'], priority=40,
                      reason=f'{ability.get("name", ability["id"])} uses a verified effective element; keep {reserve_mp} MP for healing.')
    if commands is not None and 1 not in commands:
        return Action('switch', reason='Attack is unavailable; switch to another ready character.')
    # Physical immunity/counter and elemental weapons use the same defense gate.
    physical = [e for e in enemies if not e.get('physical_immune') and not e.get('counter_physical')
                and (not actor.get('attack_elements') or (elemental_multiplier(actor['attack_elements'], e) or 0) > 0)]
    if not physical:
        return Action('wait', reason='No verified effective attack against the current enemy phase.')
    weakest = min(physical, key=lambda e: (-e.get('threat', 0), e['hp'], e['slot']))
    return Action('attack', 'enemy', weakest['slot'],
                  reason=f'Attack the lowest-HP enemy (slot {weakest["slot"]}, {weakest["hp"]} HP).')


def elemental_multiplier(elements, enemy):
    """Single-element attacks only until mixed-element precedence is verified."""
    if not elements:
        return 1
    if elements & (elements-1) or not enemy.get('affinities_known'):
        return None
    kind = enemy.get('affinities', {}).get(str(elements.bit_length()-1), 'normal')
    return {'weak': 2, 'normal': 1, 'resist': .5, 'immune': 0,
            'absorb': -1, 'recover': -1, 'death': 0}.get(kind)
