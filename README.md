# FF7 + Laya background controller

Runs a local loop: read verified FF7 memory, derive legal actions, recheck the state, and control a virtual gamepad. The default deterministic policy needs no model, Torch, network, or API key. Laya remains optional (`-Policy laya`). The controller does not send keyboard or mouse events, activate windows, or write to game memory.

**Story route is data-driven** (`routes/shinra_hq.json`, engine `route.py`): field + progress + flags select an objective. Without a route, the optional local `reference/field_story_events.json` catalogue supplies candidates checked against live field names, variables, actors and geometry. Unsupported catalogue requirements are skipped. The final fallback explores script triggers and reachable exits with bounded attempts and remembers used exits across rooms. Catalogue entries are reference guidance, not verified playthrough coverage. Unknown dialogue answers remain unanswered.

**This is a working integration with a limited story route, not a completed whole-game player.** The implemented route obtains Keycard 62 on floor 61, returns to Mayor Domino, reads the password from game state, and heads through floors 63 and 64 toward floor 65. Past that route the bot explores script triggers and exits on its own (played live up to the Midgar exit). World travel, minigames, shops, Materia setup and automatic disc switching remain unfinished. See `verification.json` for what has actually passed live tests.

## Quickstart

Windows 10/11 only. You bring the emulator, the BIOS and the game; nothing here downloads them.

**You need**

| Requirement | Notes |
| --- | --- |
| [DuckStation](https://www.duckstation.org/) | The only emulator tested live. Found automatically at `%LOCALAPPDATA%\Programs\DuckStation\duckstation-qt-x64-ReleaseLTCG.exe`; anywhere else, pass `-EmulatorPath`. |
| A PS1 BIOS image | DuckStation will not boot a game without one. Set it up in DuckStation first. |
| Final Fantasy VII, USA, Disc 1 (`SCUS-94163`) | Your own CUE/BIN or ISO. Other regions are refused (the RAM decoder checks the USA executable). Pass the path with `-Disc`. |
| [Python 3.9+](https://www.python.org/downloads/windows/) with the `py` launcher | Developed on 3.13. The launcher creates `.venv` and installs `requirements.txt` (only `vgamepad`) by itself. |
| [ViGEmBus driver](https://github.com/nefarius/ViGEmBus/releases) | Provides the virtual Xbox 360 pad the bot plays with. Install it, then reboot if DuckStation does not see the pad. |

**Set up DuckStation once**

1. Boot FF7 by hand, play past the opening until Cloud can walk, and make a save (memory card or save state). The bot does not use the title screen or the New Game / Continue menu.
2. The virtual pad only exists while the bot is running. With the game open, start the bot once (`& .\Start-FF7.ps1 -Panel -Play`); while it runs, in **Settings → Controllers → Controller Port 1** choose *Digital Controller* and use **Automatic Mapping** on the Xbox 360 pad (`SDL-0`). Expected result: Circle = `SDL-0/B`, Cross = `SDL-0/A`, Triangle = `SDL-0/Y`, Square = `SDL-0/X`, Start = `SDL-0/Start`, D-pad = `SDL-0/DPad*`, L1/R1 = shoulders. Unplug other gamepads, or make sure the virtual pad is `SDL-0`.
3. In **Settings → Hotkeys → Save States** bind **Save Game State 10** to `SDL-0/LeftStick` (milestone save states) and **Load Game State 10** to `SDL-0/RightStick` (automatic reload after a game over). Both are optional: without them the bot still plays, but makes no milestones and waits at a game over screen.

**Run**

```powershell
git clone https://github.com/Deeads/ff7-automation.git
cd ff7-automation
& .\Start-FF7.ps1 -Doctor                                  # read-only readiness report
& .\Start-FF7.ps1 -Panel -Play -Disc 'D:\Games\FF7 Disc 1.cue'
```

After the first run, double-click `FF7 Bot.cmd` (same as `-Panel -Play`): it starts DuckStation with your latest resume state if it is not open, opens the control panel at http://127.0.0.1:7777 and starts Story mode with battles on. `& .\Install-Shortcuts.ps1` adds a Desktop and Start Menu shortcut with hotkey Ctrl+Alt+F (`-Remove` deletes them). STOP in the panel or `Stop-Laya.ps1` releases the pad.

`-Disc` and `-EmulatorPath` are only read when the launcher has to start DuckStation itself; if the game is already open, it just connects. With DuckStation closed and the image somewhere other than the default path, `FF7 Bot.cmd` cannot find it: open the game first, or edit the `.cmd` to add `-Disc`.

**What to expect.** The bot walks fields, reads dialogue, fights random and scripted battles (Attack, Item, Limit, plus Cure when Restore Materia is equipped) and looks for the next story trigger by itself. It was played live from the Shinra building to the Midgar exit. It stops at the world map, does not buy from shops, does not equip Materia, and does not play minigames on purpose. `verification.json` lists what was actually seen working and what was not.

## One-command startup

Double-click `Start-FF7.cmd` (bot only, no panel), or run `& .\Start-FF7.ps1`. It connects to an existing emulator; if none is running, it starts the installed DuckStation with your latest USA Disc 1 resume state. Before loading that state it copies the resume file and FF7 memory cards into `backup-before-automation/`. It then starts story navigation with basic battle assist and reconnection enabled. `Stop-Laya.ps1` stops controller input.

On a new PC, the launcher creates a local Python environment and installs `requirements.txt` if needed. Python 3 and the ViGEmBus driver must be installed. It does not download game images, BIOS files, or model weights. Custom paths:

```powershell
& .\Start-FF7.ps1 -Doctor                    # Read-only readiness report
& .\Start-FF7.ps1 -ObserveOnly -Seconds 10    # Read game state; no input
& .\Start-FF7.ps1 -EmulatorPath 'C:\Emulators\DuckStation.exe' -Disc 'D:\Games\FF7 Disc 1.cue'
```

Startup, emulator exit/restart, and unsupported screens can remain in `waiting` with the controller released. Loading a playable save resumes detection. A detected rewind, large play-time jump, or reconnection resets transient navigation, collection and battle attempts. This does not identify every possible same-timestamp save load. Explicit low-HP stops and side-goal handovers still end a run.

| Emulator compatibility on Windows | Status |
| --- | --- |
| DuckStation, USA Disc 1 | Live RAM attachment, movement and basic battle escape tested |
| RetroArch, BizHawk/EmuHawk, Mednafen, ePSXe, PCSX | Process discovery and generic RAM scanning implemented; **not live-validated** |
| Other processes | Explicit `--pid` and `--ram-base` available for investigation; not promised compatible |

The decoder requires the verified FF7 USA executable and contiguous little-endian 2 MB PS1 RAM. Page-aligned allocations are discovered automatically; an explicit base supports other alignment. Non-USA builds, alternate memory layouts, world-map control, title/load-menu navigation, and minigames are unsupported. Bind the virtual Xbox controller to the emulator's first PS1 controller port. Do not open multiple emulators during playback: the virtual pad is shared, and the loop waits when multiple known emulator processes are detected.

## Start and stop

### Milestone save states

Automatic checkpoints are ON by default while the controller is running.
They save after the live story-progress counter advances (including major
quest transitions) or a tracked side goal completes. Several advances during
a cutscene share one checkpoint at the next stable, unlocked field screen.
Attaching to an existing save does not pretend earlier quests just completed.
Replayed milestones are deduplicated against existing checkpoint files; loading
another timeline clears pending detections. Observe mode never saves.

The control panel's **Save States** section has a Milestones toggle, **Save Now**,
and the latest saved checkpoints. Save Now also works with navigation stopped;
it creates a short-lived save worker using the same controller session lock.
It waits for a stable field screen and reports errors without claiming success.
Automatic checkpoints do not run while the bot is stopped.

Each save is a separate DuckStation `.sav` in `checkpoints/`, accompanied by
story progress, location, disc, party health, timestamp, and a SHA-256 checksum.
Earlier checkpoints are never automatically deleted. Restore with DuckStation's
**Load State → From File**, or pass its absolute path to `Start-FF7.ps1 -SaveState`
when the emulator is closed. Loading is a user action with one exception: after a game over (whole party at 0 HP in battle) the bot loads the newest milestone by itself with **Settings → Hotkeys → Load Game State 10 → SDL-0/RightStick** (`LoadGlobalState10`) and plays on. A milestone that ends in a game over three times in a row yields to the one before it. Without that binding or without any milestone the bot waits at the game over screen as before.

This save adapter supports **DuckStation with USA FF7**. One-time setup is
**Settings → Hotkeys → Save Game State 10 → SDL-0/LeftStick**. The tested
DuckStation build stores this UI binding under `SaveGlobalState10` in settings.
Do not bind that button to other actions. The feature checks for conflicting
bindings, preserves the previous slot-10 file in `checkpoints/slot-backups/`,
presses the virtual button, and only reports success after a newly written,
complete FF7 save file has been copied into the archive. Other emulators report
unsupported instead of sending this hotkey. For a custom/portable DuckStation
installation, set `duckstation_data` to its data directory in the local
`checkpoint-config.json`; the SaveStates folder is read from its settings.ini.
Save-format checks follow DuckStation's [save-state header](https://github.com/stenzek/duckstation/blob/master/src/core/save_state_version.h).

Open PowerShell in this folder, with FF7 already loaded in DuckStation:

```powershell
& .\Start-Laya.ps1                    # Supported story objectives; hidden background process
& .\Stop-Laya.ps1                     # Release input and stop
Get-Content .\laya-status.json        # Current objective, waiting reason, or error
```

**Run / Cross and your `2` macro.** Field movement holds the d-pad and Cross (Xbox A on the virtual pad) together in one report, which is the same as your DuckStation keyboard `X` / hold-to-run `2` macro; the bot never sends keystrokes. It walks the last ~30 units before an interaction point, and before any Circle (dialogue, menu, inspect) it releases Cross and waits a 100 ms edge, because Cross means cancel there. **Do not press the `2` macro while the bot runs**: a doubled Cross is harmless while running but will cancel dialogue and menus. `-Walk` disables run.

Movement is executed deterministically: Laya approves each new movement objective once (and again after a stall); the executor then re-reads state about every 30 ms and rewrites the held direction without releasing the pad until arrival, interaction, a field/mode change, a script lock/fade, an unrecoverable stall (one sidestep is tried first), STOP, or an error. Each `decisions.jsonl` entry for movement logs `state_checks`, `run_checks`, `held_s`, `cross_held_pct`, `direction_changes`, `alternates`, and `macro_end`.

**Chests and pickups.** Before taking a field exit, the bot opens reachable unopened chests/pickups found in the loaded field's own scripts (item/materia/gil grant + the game's "taken" flag; `field_script.py`, read-only). Success is only the flag flipping; after 2 failed tries a treasure is skipped and logged (`collect_skipped` in `decisions.jsonl`). Scripts that ask a question are never collected. Save points are detected but **not used** (saving would overwrite a memory-card slot). Disable with `-NoCollect`.

**Field healing.** Between battles, when anyone is KO or below `-HealAtHp <percent>` of max HP (default 50, `0` = off), the bot opens the main menu and recovers the party before doing anything else: Phoenix Down on a KO member (a Restore-materia holder first), then Cure from the living Restore holder with the most MP on whoever is lowest, always keeping one Cure's MP for the next battle, and Potion/Hi-Potion when nobody can cast. Once the menu is open it tops everyone up to 75%, then closes it. Every press is chosen from the menu's own cursor bytes (`field_menu.py`, live-verified on the USA build), anything unexpected is backed out of with Cross, and the HP change is checked after each use: two uses without effect, a menu that will not open after three tries, or 80 inputs in one visit turn healing off until the next field or battle (`heal_blocked` in `laya-status.json`). It does not use Tents, save points, inns, Ethers, Cure2/3, Life, or change equipment. 

**Fetching a Phoenix Down.** With story navigation on, when someone is KO and no Phoenix Down is in stock, the bot looks for one instead of stopping: `supplies.py` reads every field's scripts from your Disc image (cached in `field-index.json`), finds pickups that grant a Phoenix Down and whose "taken" flag is still clear in the live save, and walks there through ordinary field exits (nearest first, at most 8 fields, 10 minutes). It then opens the pickup, and field healing revives the member. `-StopAtHp` ignores KO members while this is in progress; living members still trigger it. It only follows gateway exits: no elevators, scripted doors, shops, enemy drops or world map. If nothing is reachable, or the pickup cannot be opened in two tries, it gives up (`supply_blocked` in `laya-status.json`) and the low-HP stop applies again. Lockers and shelves that are line triggers (stand on the line, Circle) are now opened by the normal chest collection too.

**Resting when healing runs out.** With story navigation or `-Grind`, when the best living Restore holder has less MP than two Cures (or less than `-RetreatAtMp <percent>` of max MP), or someone is under the heal threshold with nothing left to heal them, the bot walks to the nearest verified rest spot and rests. Rest spots are entities whose field script restores HP/MP (found from the disc); only ones whose dialogue answers were verified live are used. Currently that is the Shinra 64F nap room next to the save point (`grind.py`, `REST`): full HP/MP, no Gil. Away from a retreat the bot answers "Let's hurry on". No reachable rest spot is reported as `rest_blocked`. Tents and save points are not used.

**Finding the next step without a route.** The field script itself is read for hints: a trigger whose script writes story progress or starts a scripted battle, and is gated on the current progress value, is tried before anything else; a walk-over line that also waits for a button (IFKEY) is treated as "stand on it and press Circle" (live 2026-10-12: the glass elevator controls, `eleout` `lin0`). Exploration no longer parks: when every trigger and exit was tried it forgets all attempts and searches again (`Search 2`, `Search 3`, ...); it only waits when a field offers nothing at all. A message that never closes ("End with the (START) button") gets the button it names after 6 s, otherwise Circle, Cross and Start in turn (`dialog_nudge` in `decisions.jsonl`). A screen the bot cannot read that stays unchanged for 20 s gets Cross, at most 6 times (`screen_nudge`; this leaves shop menus). Two such screens are recognised: "Please make a party of three" is filled with a bounded random walk until the save shows three members, and the bike chase gets alternating sword swings. The time allowed for one trigger grows with the walking distance to it.

**When the route does nothing (unsure mode).** A route step that changes nothing after 6 inputs, sits on a reached waypoint for 5 s, or cannot be walked to is dropped for that field and story progress (`objective_no_effect` in `decisions.jsonl`), and the bot looks around instead: every reachable trigger line is walked onto (stepping 70 units away first, because contact lines only fire on entry) and stood on with Circle, door lines included; every visible model is checked, from the nearest floor if it is on a bed or behind a counter; then exits, least-visited first, each field's exits reopened at most 3 times. A trigger that produces dialogue is repeated up to 4 times. Dialogue choices with no verified answer are tried one option per visit, top to bottom (`unverified_choice` events); verified answers always win. `--ask-choices` restores waiting for you at unknown choices. A stalled movement is retried every 15 s instead of waiting for a new position.

**One-click start.** Run `Install-Shortcuts.ps1` once: it puts **FF7 Bot** on the Desktop and in the Start Menu (Win key, type `ff7`) with the hotkey Ctrl+Alt+F. Opening it (or double-clicking `FF7 Bot.cmd`) starts DuckStation with FF7 if no emulator is open, opens the control panel in its own window and starts Story mode with battles on; STOP in the panel halts it and the other modes (Assist, Grind, Observe) start from there. Remove `-Play` in `FF7 Bot.cmd` to only open the panel. Live 2026-10-12: emulator closed, one click, bot playing after the game loaded.

**Limit Breaks.** Any character's first Limit is used when the bar is full. The target step follows the game: a cursor that opens on the enemies takes an enemy, one that opens on the party (support Limits such as Healing Wind) is confirmed as is. Limit input screens after that, such as Tifa's slot reels, are stopped with Circle; reels are not timed for Yeah!/Hit.

**Hidden or unselectable enemies.** An enemy that is invisible, retracted or a helper part is never attacked; with nothing to target, the turn goes to healing anyone under 70% HP, otherwise the bot waits. If the unit flags call an enemy targetable but the cursor cannot reach it, Attack and Limit take another selectable enemy after 4 cursor moves, and that slot is left out of targeting for 8 seconds; other actions back out and replan after 12 moves.

**Grind test.** `-Grind` (`--grind`) goes to the nearest field with random encounters (busier table first), paces across it away from the exits, fights with the battle strategy, heals in the field, and retreats to rest as above. It implies battle assist and counts fights in `grind_battles`.

**Low-HP stop.** `-StopAtHp <percent>` stops Laya (pad released, `laya-status.json` shows `blocked` / `low_hp` with who and how low) as soon as any party member is below that share of max HP, KO included. It uses battle HP during fights and save HP elsewhere, and is checked before any button is pressed each tick. It is a hand-over, not a pause: if it triggers mid-battle the enemies keep acting until you take the controller. Off by default. Outside battle it only triggers once field healing has nothing left it can do.

Stop the current session before starting another. A Windows file lock prevents two Laya sessions from controlling the game. The old `controller_test_session.py` helper must also be closed. Existing macros are preserved; avoid running gameplay macros or manually controlling FF7 while Laya is acting.

Optional modes:

```powershell
& .\Start-Laya.ps1 -ObserveOnly -Seconds 30  # Decisions only; no virtual controller
& .\Start-Laya.ps1 -AssistOnly              # Ordinary menus and dialogue, no story navigation
& .\Start-Laya.ps1 -Target 242,805,50 -Seconds 10  # A coordinate on this particular field
& .\Start-Laya.ps1 -StopAtHp 30               # Stop and hand over when anyone drops below 30% HP (KO counts)
& .\Start-Laya.ps1 -HealAtHp 0                # Never open the menu to heal between battles
& .\Start-Laya.ps1 -Grind -RetreatAtMp 50     # Fight random battles; rest when the healer is under half MP
```

The console entry point is `.venv\Scripts\python.exe laya_loop.py`. Without `--execute`, it only observes; without `--story` or `--target`, it has no navigation objective. The PowerShell launcher enables `--reconnect` by default; `-FailFast` retains the original stop-on-unsupported behavior. Select discovery with `-Emulator duckstation` (or another family), `-EmulatorPid 1234`, or `-RamBase 0x12340000`. The generic scan is bounded and refuses ambiguous divergent RAM copies.

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
- **Battles.** Battles are fought by default from `Start-Laya.ps1` and the control panel (Battle ON). With `-NoBattle` / Battle OFF the bot does nothing in a battle and **you must fight it yourself** (enemies keep acting; an unattended battle ended in a game over on 2026-09-29). With `-BattleAssist`: Circle-attack with the default target, a 3 s L1+R1 escape at critical HP in escapable battles (at most 2 tries, then keep attacking), never escape inescapable battles. The full strategy (`battle_strategy.py`: Phoenix Down on KO, Cure with Restore materia or Potion/Hi-Potion under 40%, Limit when ready, attack lowest-HP enemy) only takes over once the menu cursor addresses are live-verified in `battle_menu.json`; find them with `-BattleProbe` (harmless menu moves, backs out) and check against `-Capture` screenshots (`battle-captures/`, PrintWindow, no focus change). Every battle is logged raw + decoded to `battle-log.jsonl`. After a battle the bot presses Circle through the EXP/Gil/level-up screens until the field returns (game over: it stops instead). It never confirms an attack while no enemy is targetable (retracted/hidden enemies, helper parts), because the default target would be an ally; it backs out with Cross instead.

## Discs

**Disc 1 alone is supported.** The control panel lists all three discs as Available, Missing, or Invalid image. Missing Discs 2 and 3 do not prevent starting or playing Disc 1. Expand a disc row to add a local CUE, BIN or ISO path; the boot serial is checked before saving it to `discs.json`. The panel also discovers FF7 CUE/ISO files in the known game folder and configured image folders.

If an unsupported screen's readable save state requires a missing Disc 2 or 3, the panel explains which image to add. This is a save-state-based hint, not a verified visual disc-prompt detector. Load the required image through your emulator; automatic swapping is not implemented. Adding an image never swaps the current disc or alters a game image. CHD/PBP validation is not implemented.

When all three USA images are available, you can additionally create an ordered playlist:

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

Core dependency: vgamepad 0.1.0 plus ViGEmBus. Optional `--battle-probe`/`--capture` need NumPy. Optional Laya policy uses Laya 0.3.20 and CPU Torch, with model cache `convaiinnovations/laya`, revision `55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`. Model probabilities and deterministic decision scores are not measured gameplay success rates.

Run the checks:

```powershell
& .\.venv\Scripts\python.exe -m unittest -v
& .\.venv\Scripts\python.exe ff7_state.py
```

References: [Laya source/API](https://github.com/NandhaKishorM/laya), [FF7 PSX reversing data](https://github.com/q-gears/q-gears-reversing-data), [FF7 savemap](https://ff7-mods.github.io/ff7-flat-wiki/FF7/Savemap.html), [dialogue windows](https://ff7-mods.github.io/ff7-flat-wiki/FF7/Field/DialogWindow.html), [DuckStation command-line arguments](https://github.com/stenzek/duckstation/wiki/Command-Line-Arguments), [DuckStation hotkeys](https://github.com/stenzek/duckstation/blob/master/src/core/hotkeys.cpp).


## Not included in this repo

Game data is not redistributed here: RAM snapshots, memory cards, save files, and extracted field (`.evt`) files are Square Enix's. Some tests depend on RAM fixtures you'll need to capture from your own copy of the game. The local model weights are downloaded separately into `models/`, and the ViGEmBus driver comes from its own installer (https://github.com/nefarius/ViGEmBus/releases).

## Credits

Memory addresses, field/battle structures, and the savemap layout were worked out with help from the FF7 reverse-engineering community: the PSX decompilation notes, the FF7 TAS Lua scripts, and the DuckStation emulator source. Those references are not re-hosted here; credit belongs to their authors.
