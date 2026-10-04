# FF7 + Laya background controller

Runs a local loop: read verified FF7 memory, derive legal actions, ask Laya to select an action, recheck the state, and briefly press a virtual controller button. It does not send keyboard or mouse events, activate windows, or write to game memory. You can type in other apps while it plays.

**Story route is data-driven** (`routes/shinra_hq.json`, engine `route.py`): each step = field + progress range + `when` flags -> target (gateway, script line, actor, inspect/OK-line, duct hold, puzzle) + an `until` verification flag, so reloading a save resyncs. Dialogue answers live in the same file. No matching step = `blocked`, never a guess. Steps marked `"source": "script"` come from the game's own field scripts but have not been live-tested yet.

**This is a working integration with a limited story route, not a completed whole-game player.** The implemented route obtains Keycard 62 on floor 61, returns to Mayor Domino, reads the password from game state, and heads through floors 63 and 64 toward floor 65. Later story sections, combat strategy, world travel, minigames, and automatic disc switching remain unfinished. See `verification.json` for what has actually passed live tests.

## Start and stop

Open PowerShell in this folder, with FF7 already loaded in DuckStation:

```powershell
& .\Start-Laya.ps1                    # Supported story objectives; hidden background process
& .\Stop-Laya.ps1                     # Release input and stop
Get-Content .\laya-status.json        # Current objective, waiting reason, or error
```

**Run / Cross and your `2` macro.** Field movement holds the d-pad and Cross (Xbox A on the virtual pad) together in one report, which is the same as your DuckStation keyboard `X` / hold-to-run `2` macro; the bot never sends keystrokes. It walks the last ~30 units before an interaction point, and before any Circle (dialogue, menu, inspect) it releases Cross and waits a 100 ms edge, because Cross means cancel there. **Do not press the `2` macro while the bot runs**: a doubled Cross is harmless while running but will cancel dialogue and menus. `-Walk` disables run.

Movement is executed deterministically: Laya approves each new movement objective once (and again after a stall); the executor then re-reads state about every 30 ms and rewrites the held direction without releasing the pad until arrival, interaction, a field/mode change, a script lock/fade, an unrecoverable stall (one sidestep is tried first), STOP, or an error. Each `decisions.jsonl` entry for movement logs `state_checks`, `run_checks`, `held_s`, `cross_held_pct`, `direction_changes`, `alternates`, and `macro_end`.

**Chests and pickups.** Before taking a field exit, the bot opens reachable unopened chests/pickups found in the loaded field's own scripts (item/materia/gil grant + the game's "taken" flag; `field_script.py`, read-only). Success is only the flag flipping; after 2 failed tries a treasure is skipped and logged (`collect_skipped` in `decisions.jsonl`). Scripts that ask a question are never collected. Save points are detected but **not used** (saving would overwrite a memory-card slot). Disable with `-NoCollect`.

**Low-HP stop.** `-StopAtHp <percent>` stops Laya (pad released, `laya-status.json` shows `blocked` / `low_hp` with who and how low) as soon as any party member is below that share of max HP, KO included. It uses battle HP during fights and save HP elsewhere, and is checked before any button is pressed each tick. It is a hand-over, not a pause: if it triggers mid-battle the enemies keep acting until you take the controller. Off by default.

Stop the current session before starting another. A Windows file lock prevents two Laya sessions from controlling the game. The old `controller_test_session.py` helper must also be closed. Existing macros are preserved; avoid running gameplay macros or manually controlling FF7 while Laya is acting.

Optional modes:

```powershell
& .\Start-Laya.ps1 -ObserveOnly -Seconds 30  # Decisions only; no virtual controller
& .\Start-Laya.ps1 -AssistOnly              # Ordinary menus and dialogue, no story navigation
& .\Start-Laya.ps1 -Target 242,805,50 -Seconds 10  # A coordinate on this particular field
& .\Start-Laya.ps1 -StopAtHp 30               # Stop and hand over when anyone drops below 30% HP (KO counts)
```

The console entry point is `.venv\Scripts\python.exe laya_loop.py`. Without `--execute`, it only observes; without `--story` or `--target`, it has no navigation objective. Runtime inference is offline and needs no API key. The hidden launcher runs until stopped, blocked, or an error occurs.

## Control panel

```powershell
& .\Start-UI.ps1    # starts a hidden local server and opens http://127.0.0.1:7777
& .\Stop-UI.ps1     # closes the panel server (does not stop the bot)
```

PS1-style panel: pick Story / Assist / Observe, toggle Run, Battle assist and duration, then START / STOP (Enter / Esc). Shows party HP/MP, objective, what the bot is doing, a live walkmesh map (Cloud, exits, target, NPCs/objects) and the recent decision log. It only reads the bot's status files and calls `Start-Laya.ps1` / writes `STOP`; it never sends input to the game. Localhost only.

## Implemented behavior

- Read-only RAM adapter verifies the FF7 USA kernel fingerprint and structure bounds. Extracts live field coordinates, full walkmesh adjacency, locked triangles, visible characters and collision radii, gateways, dialogue text/cursor, party records, and story inventory.
- A* navigation follows connected unlocked triangles. Doorway waypoints account for solid characters. It stops after repeated actions without progress; it cannot guarantee a route around every dynamic obstacle.
- Story objectives come from current key items and field IDs, so reloading an older save recalculates the objective. Known dialogue choices use the actual cursor row. Unrecognized story choices/routes stop with a `blocked` status.
- Laya selects between waiting and an action proposed by navigation/dialogue logic. Geometry and the story route remain deterministic code; Laya is not independently reasoning through the whole game.
- Every action is checked again against fresh game state. A persistent virtual pad holds movement continuously (see above), sends bounded button presses otherwise, and releases in `finally`. The `STOP` file is checked during input holds and between decisions. Model inference runs with no buttons held.
- **Battles.** Without `-BattleAssist` the bot does nothing in a battle and **you must fight it yourself** (enemies keep acting; an unattended battle ended in a game over on 2026-09-29). With `-BattleAssist`: Circle-attack with the default target, a 3 s L1+R1 escape at critical HP in escapable battles (at most 2 tries, then keep attacking), never escape inescapable battles. The full strategy (`battle_strategy.py`: Phoenix Down on KO, Cure with Restore materia or Potion/Hi-Potion under 40%, Limit when ready, attack lowest-HP enemy) only takes over once the menu cursor addresses are live-verified in `battle_menu.json`; find them with `-BattleProbe` (harmless menu moves, backs out) and check against `-Capture` screenshots (`battle-captures/`, PrintWindow, no focus change). Every battle is logged raw + decoded to `battle-log.jsonl`. After a battle the bot presses Circle through the EXP/Gil/level-up screens until the field returns (game over: it stops instead). It never confirms an attack while no enemy is targetable (retracted/hidden enemies, helper parts), because the default target would be an ally; it backs out with Cross instead.
- Experimental battle decoding and simple confirm/escape assistance exist behind `-BattleAssist`. They have synthetic tests but **have not been validated in a live battle**. This is not a healing, boss, spell, inventory, or target-selection strategy. It is disabled by default.

## Discs

Only Disc 1 is currently available in the known game folder. When all three USA images are on this PC, validate them and create an ordered playlist:

```powershell
& .\.venv\Scripts\python.exe discs.py 'PATH TO DISC 1.cue' 'PATH TO DISC 2.cue' 'PATH TO DISC 3.cue'
```

This checks the boot serial inside each image before creating `Final Fantasy VII.m3u` and `discs.json`. CUE/BIN (data track starting at zero) and ISO are supported. The manifest explicitly remains `runtime_ready: false`: preparing a playlist does not configure DuckStation or implement automatic switching. Live disc-prompt detection, hotkey setup, and an actual swap test still need completion. Existing saves/discs are not changed by this command.

## Status and diagnostics

- `live-state.json`: latest observed state, including full navigation geometry. Party HP/MP is labeled as persistent save data, separate from experimental battle combatants.
- `decisions.jsonl`: append-only decisions, probabilities, applied actions, rejection reasons, timings, and foreground-window comparison.
- `laya-status.json`: loading/running/stopped/blocked/error and the current objective. An error or blocked state releases the controller.
- `laya-output.log`, `laya-errors.log`: hidden process output.
- `verification.json`: recorded live evidence and current limitations.

Local dependencies: Laya 0.3.20, vgamepad 0.1.0, CPU Torch. Model cache: `convaiinnovations/laya`, revision `55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`. Observed inference is roughly 0.4–0.7 seconds on this machine, plus input and state-reading time. A 0.125-second scheduling interval does not mean eight model decisions per second. Model probabilities are not measured gameplay success rates.

Run the checks:

```powershell
& .\.venv\Scripts\python.exe -m unittest -v
& .\.venv\Scripts\python.exe ff7_state.py
```

References: [Laya source/API](https://github.com/NandhaKishorM/laya), [FF7 PSX reversing data](https://github.com/q-gears/q-gears-reversing-data), [FF7 savemap](https://ff7-mods.github.io/ff7-flat-wiki/FF7/Savemap.html), [dialogue windows](https://ff7-mods.github.io/ff7-flat-wiki/FF7/Field/DialogWindow.html), [DuckStation hotkeys](https://github.com/stenzek/duckstation/blob/master/src/core/hotkeys.cpp).


## Not included in this repo

Game data is not redistributed here: RAM snapshots, memory cards, save files, and extracted field (`.evt`) files are Square Enix's. Some tests depend on RAM fixtures you'll need to capture from your own copy of the game. The local model weights are downloaded separately into `models/`, and the ViGEmBus driver comes from its own installer (https://github.com/nefarius/ViGEmBus/releases).

## Credits

Memory addresses, field/battle structures, and the savemap layout were worked out with help from the FF7 reverse-engineering community: the PSX decompilation notes, the FF7 TAS Lua scripts, and the DuckStation emulator source. Those references are not re-hosted here; credit belongs to their authors.
