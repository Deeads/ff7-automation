# Battle strategy design

Implementation status: advanced spell control enabled on the verified USA
native menu profile. Cure and Bolt were live-tested against HP/MP changes.
Emergency healing, healer switching, elemental defenses, MP reserves, persistent
plans, and target feedback are implemented. Since 2026-10-11 the Item list
(Potion live-tested) and the first Limit of attack-Limit characters (Sled Fang
live-tested) are enabled; Phoenix Down on a KO ally and scrolling spell pages
are not live-verified.

Audit 2026-10-11 of the 2026-10-10 party wipe (battle-log.jsonl, decisions.jsonl):
a 117 s stall when Limit replaced Attack (fixed that night), then no recovery
once the only Restore holder was KO because items were disabled, three fled
battles in a row with a healthy survivor, and no healing between battles. Fixes:
battle items, Limit use, flee only when a survivor is critical or alone and the
enemies are above 20% total HP, no flee while another member can still heal,
healer switch gives up after three ignored Triangles, an unreachable target
cursor is backed out of (12 moves) instead of waited on, and field healing,
Phoenix Down fetching and resting between battles.
Scope: the verified FF7 USA PSX build, initially DuckStation. Other emulator
adapters must supply the same validated observations before sharing this policy.

## Outcome

Finish encounters while preserving the party and enough healing resources for
the next encounter. Survival takes priority over speed, optional steals, or AP.
No strategy guarantees survival from every loaded save, equipment set, or boss
state. The controller must distinguish unavailable actions from missing telemetry.

## What the existing code actually supports

- `battle.py` reads live combatant HP/MP, basic status/visibility flags, targets,
  Limit readiness, and escape difficulty. Battle health, not stale save HP, must
  drive combat decisions.
- `battle_data.py` reads active spells and loaded enemy elemental defenses.
- `battle_strategy.py` prioritizes emergency recovery before revival, reserves
  healing MP, avoids reflected/absorbed spells, and selects effective offense.
  When Limit replaces Attack, a verified effective spell or another character
  can take the turn. Unknown Limit effects are never assigned an enemy target.
- `battle_menu.py` uses the live-verified `battle_menu.json` native cursor profile.
- `battle_control.py` preserves plans across menu navigation, validates resources
  and targets, learns target movements from feedback, and reserves pending heals
  until their HP outcome is observed. Only verified menu capabilities are offered.

The remaining sections describe the broader design, including future features;
they are not a claim that every tactic below is enabled.

## Data flow and ownership

```text
Read-only RAM + owned KERNEL/SCENE data
                |
     validated BattleSnapshot
                |
       legal action generation
                |
       survival/threat scoring
                |
        committed ActionPlan
                |
  fresh-state menu/target navigation --> virtual controller
                |
   execution + outcome observation --> history/resource reservations
```

Keep the planner pure and deterministic in `battle_strategy.py`. Extend
`battle.py` for observations and `battle_menu.py` for feedback-driven execution.
Add small battle-data and session modules only for extracted metadata and pending
transactions. Do not introduce a model call into each movement or command tick.

Required observations, each with source and validity:

| Observation | Purpose |
| --- | --- |
| Encounter generation, formation, actor ID and slot | Reject stale plans after a reload or party change; encounter number alone can repeat |
| Current HP/MP, statuses, controllability, ATB/readiness | Decide who can act and who is in immediate danger |
| Actual available spells/items/commands and their costs | Avoid inferring unlocked magic from materia ID alone; account for AP, support links and restrictions |
| Enemy element responses and status susceptibility | Distinguish weakness, normal, resistance, immunity, absorption and recovery |
| Target groups/masks, cursor, grid geometry, scroll position | Confirm the intended legal target, including KO allies and multi-target actions |
| Action queue and outcomes, where verified | Avoid duplicate healing/revival and associate costs with accepted commands |

Extract static attack, item, and enemy data from the user's owned game. Key caches
by game build and data hashes. Prefer verified live overrides for phase changes.
Unknown affinity is unknown, never an assumed weakness. The existing local
`reference/ffvii_battle_ffvii_address.txt` describes active spell lists, elemental
responses and unit records; these are investigation leads until live-validated.

## Decision priorities

The percentages below are initial policy settings to tune through replay and live
tests, not claims about FF7 damage formulas.

1. **Validate the turn.** A controllable actor, legal command, and legal target
   must exist. Keep hidden/helper enemies out of target selection. Do not stall
   the whole party merely because one character cannot act.
2. **Prevent imminent defeat.** Heal a living member likely to fall before the
   next recovery action. Start emergency healing below 25% HP; also trigger it
   above that threshold when observed incoming damage makes the next hit lethal.
   Unknown damage uses the conservative HP threshold, not zero predicted damage.
   Keeping the last living actor alive can outrank reviving someone else.
3. **Restore the party.** Revive a KO ally when the survivor can remain safe;
   prioritize the only healer or another immediately useful actor. Reserve one
   revival per target and follow with healing when needed. Never issue two
   Phoenix Downs for the same KO based on stale snapshots.
4. **Remove disabling effects.** Treat urgent effects according to their verified
   mechanics and remaining time. Restore a disabled healer when that unlocks
   recovery; choose an item when magic is unavailable. Do not repeatedly attempt
   a status cure that the available action cannot remove.
5. **Maintain a health buffer.** Initial routine healing threshold: 45%; raise it
   to 60% during verified dangerous boss phases. Choose the smallest legal heal
   that reaches a useful buffer. Compare expected recovery, MP, inventory, turn
   cost, and waste. Stronger healing can outrank a cheap ineffective Potion.
6. **Reduce enemy actions.** Finish a dangerous target when a conservative damage
   estimate predicts a kill. Otherwise focus useful damage on threats. Lowest HP
   is a tie-breaker, not the sole definition of priority. Phase/counter rules may
   prohibit attacking an otherwise vulnerable target.
7. **Exploit verified weaknesses.** Compare legal elemental spells and physical
   attacks using expected damage, accuracy, resource cost, and known reactions.
   Reject spells that heal or are nullified by that target. Unknown elemental
   precedence, reflection, or multi-element behavior prevents that optimization
   until verified. Recheck defenses at execution time.
8. **Use support when it pays off.** Consider damage mitigation, speed buffs,
   useful debuffs, summons, Enemy Skills, and Limits only when their actual
   availability and effect are known. A healing Limit must not be sent through
   the generic enemy-attack path. Skip ineffective repeat buffs/debuffs and long
   or expensive actions when a cheaper action will finish the encounter.
9. **Escape when survival requires it.** Only for a verified eligible random
   encounter, with escape allowed and no better recovery. Never infer that an
   unknown encounter is a random battle. Bound attempts and return to a concrete
   survival plan after failure; do not keep holding escape indefinitely.

Reserve enough MP for an available emergency heal before choosing optional
offensive magic. Allow emergency resource spending to exceed normal reserves.
Do not use scarce endgame recovery items, summon every turn, or spend long turns
stealing by default. Resource preferences can become explicit settings later.

## Action transactions

Represent a plan by encounter generation, actor identity, command/ability ID,
target side and IDs, required resources, reason, expiry, and expected observation.
Persist the plan through command -> submenu -> target -> execution -> outcome.

- Re-read the state before every press. Validate the selected command, ability,
  target group, and cursor before confirming. Track target cycles and cancel
  after one unsuccessful bounded traversal instead of looping forever.
- Re-evaluate urgency while navigating. Before commitment, cancel/back out and
  replan if the actor becomes disabled, a target dies/disappears, resources change,
  or a higher-priority emergency occurs. An already queued action is not assumed
  cancellable; account for it when planning subsequent turns.
- Reserve expected healing, revival, items, and MP for queued actions. Release
  reservations on observed completion, explicit failure, timeout, or timeline
  reset. Unknown outcome requires a fresh inventory/queue check before retrying
  consumables. An enemy healing itself must not be mistaken for our heal landing.
- Distinguish command acceptance from damage/healing success. Revalidate live
  numbers after animations rather than assuming an input succeeded.
- Log the observation, plan, input, resulting cursor/phase, and outcome. Every
  blocked action must name the missing observation or unavailable capability.
- STOP releases the controller immediately; all reads remain read-only. Never
  overwrite a save, mutate HP/MP, or alter equipment through RAM writes.

## Between encounters

Implemented for the main menu's Item and Magic screens (`field_heal.py`,
`field_menu.py`; Phoenix Down, Cure, Potion, Hi-Potion): revive and restore a
minimum health buffer before walking into another encounter. Verify healing inventory and at least one usable
recovery option before a known boss trigger. Review materia, unlocked spells and
equipment against that encounter's known threats. Equipment and materia changes
must also use verified menus and confirm resulting stats. Do not silently rest,
purchase supplies, or change the user's setup without a route and resource policy.

## Implementation order and proof

| Stage | Concrete work | Acceptance evidence |
| --- | --- | --- |
| 1: Control | Verify actor, phase, command/submenu grids, scroll and ally/enemy target switching; add transactions | One observed Attack, single-target heal, and KO-target revival with exact intended targets and resources |
| 2: Survival | Emergency-before-revival ordering, recovery availability, reservations and outcome checks | Replay last-survivor danger, two KOs, no MP, no items, disabled healer, moving targets and stale snapshots; no duplicate consumables |
| 3: Weaknesses | Owned data extraction, active spell availability, defense overrides and scoring | Tests for weak/resistant/immune/absorbing/unknown targets; live spell consumption and damage agree with the chosen action |
| 4: Tactics | Per-ability Limits, boss counters/phases, support, MP reserves and post-battle recovery | Representative random encounters and boss phases; confirmed legal targeting, bounded recovery and documented losses |

Stage 1 is the next implementation task. Enable each verified capability
individually; do not mark an entire cursor map verified after finding one moving
byte. Keep basic-assist and advanced-strategy status distinct in the UI.

Regression scenarios must include loading a save during a submenu, reusing a
battle ID, changing party members, recruitment stat recalculation, hidden enemies,
an actor with a non-offensive Limit, inventory exhaustion, and STOP during a hold.
Use synthetic fixtures for decisions and owned captures for adapter checks; live
tests establish controller behavior. Report survival rate by tested encounter,
avoidable KOs, wasted resources, wrong-target confirmations, retries and stalls.
No whole-game success claim follows from a handful of successful battles.

## Trade-offs

Deterministic priorities are explainable and fast, but need encounter data and
careful ordering. Start with conservative damage bounds; add a short look-ahead
only if replay failures show the simpler policy loses winnable fights. Static
enemy data reduces startup work, but live phases can override it. Holding a plan
stabilizes menu navigation, while emergency cancellation keeps it responsive.
Revisit thresholds and resource reserves after collecting outcomes rather than
tuning to one boss or the current party alone.
