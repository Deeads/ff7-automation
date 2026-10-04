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


@dataclass(frozen=True)
class Action:
    command: str          # attack | limit | magic | item | flee | wait
    target_side: str = 'enemy'
    target_slot: int = -1
    item: int = -1
    spell: int = -1
    reason: str = ''


def ratio(unit):
    return unit['hp']/unit['max_hp'] if unit['max_hp'] else 0


def choose(battle, actor_slot, party, items, boss=False):
    """actor_slot: battle slot (0-2) whose command menu is open.
    party: save records in the same slot order (materia, MP).
    Never flees an inescapable/boss battle."""
    players = battle['players']
    actor = next((p for p in players if p['slot'] == actor_slot), None)
    if actor is None or not actor['alive']:
        return Action('wait', reason='Active character is not a living party member.')
    member = party[actor_slot] if actor_slot < len(party) else {}
    enemies = [e for e in battle['enemies'] if e.get('targetable', e['alive'])]
    living = [p for p in players if p['alive']]
    dead = [p for p in players if not p['alive']]
    escapable = battle.get('escapable', False) and not boss

    if dead and items.get(PHOENIX_DOWN, 0) > 0:
        return Action('item', 'ally', dead[0]['slot'], item=PHOENIX_DOWN,
                      reason=f'Revive KO ally in slot {dead[0]["slot"]} with Phoenix Down.')
    hurt = sorted((p for p in living if ratio(p) < HEAL_BELOW), key=ratio)
    if hurt:
        target = hurt[0]
        if RESTORE_MATERIA in member.get('materia', ()) and actor['mp'] >= CURE_MP:
            return Action('magic', 'ally', target['slot'], spell=CURE_SPELL,
                          reason=f'Cure ally in slot {target["slot"]} ({target["hp"]}/{target["max_hp"]}).')
        for item in (POTION, HI_POTION) if target['max_hp']-target['hp'] <= 150 else (HI_POTION, POTION):
            if items.get(item, 0) > 0:
                return Action('item', 'ally', target['slot'], item=item,
                              reason=f'Heal ally in slot {target["slot"]} with item {item}.')
    critical = dead or any(ratio(p) < CRITICAL_BELOW for p in living)
    if critical and escapable:
        return Action('flee', reason='Party health critical in an escapable random battle; no heal available.')
    if not enemies:
        return Action('wait', reason='No targetable enemy (hidden, retracted or helper parts only).')
    weakest = min(enemies, key=lambda e: (e['hp'], e['slot']))
    if actor.get('limit_ready'):
        return Action('limit', 'enemy', weakest['slot'], reason='Limit Break is ready.')
    return Action('attack', 'enemy', weakest['slot'],
                  reason=f'Attack the lowest-HP enemy (slot {weakest["slot"]}, {weakest["hp"]} HP).')
