# Task: make the FF7 Laya bot move continuously, run, handle checks/puzzles, then extend routes + combat

Repo: this folder (`ff7-automation`). Read `README.md`, `verification.json`, `laya_loop.py`, `motion.py`, `story.py`, `navigation.py`, `battle.py`, `ff7_state.py` first. Keep all existing safety rules: read-only RAM, virtual vgamepad only (never keyboard/mouse events, never focus the window), release in `finally`, honor the `STOP` file, one session via the lock.

## Symptoms (from live runs + `decisions.jsonl`)
1. **Stop-start movement ("presses up every now and then").** Each loop tick asks Laya (~400–600 ms inference), then `follow_route` holds the stick for at most 2 s and releases in `finally`. Result: move → release → ~0.5 s dead time → move. Laya is also re-asked for every waypoint even though navigation already picked the direction.
2. **Run is inconsistent.** `Controller.set_movement` ORs Cross (Xbox A = `cancel`) for run, but `movement_command` only runs when far from the target, and every release drops it. Many entries show `run_checks: 0`.
3. **Ignores checks / puzzles.** `story.objective` only knows key items. It never opens ordinary treasure chests, picks up items, or uses save points, and it stalls on the floor 65 Midgar model puzzle (`midgar_model_objective`) — last session stopped on field 248 cycling between "Inspect PARTA" waits and short moves.
4. No route past floor 66, no real combat.

## User's macro setup (must be respected)
- In DuckStation, keyboard **X** = Cross (run / cancel). Keyboard **2** is a user macro hotkey that *holds* Cross (hold-to-run).
- Do NOT send X or 2 as keystrokes — that would type into whatever window has focus. Reproduce the same effect on the virtual pad: hold Xbox A (Cross) together with the d-pad while running.
- Before any interaction/dialogue/menu input, fully release Cross first (Cross = cancel there), wait one release edge (~100 ms), then press Circle.
- Add a README note: don't press the `2` macro while the bot runs (double-held Cross is harmless for running but will cancel dialogue).

## Phase 1 — continuous held movement (do this first)
- Replace per-waypoint Laya calls with a **deterministic movement executor**: once an objective's target is set, run a loop that re-reads state every ~30 ms, recomputes `target_action`, and updates the held direction + Cross in a single pad report **without releasing between samples** while the direction stays valid. Change direction by rewriting the report, not by release/re-press.
- Laya is consulted only at decision points: new objective, dialogue choice, menu, battle, or when navigation is ambiguous/stalled. Movement itself should not wait on inference.
- Only release the pad when: arrived, interaction needed, field/mode change, movement lock/fade, stall (>0.6 s no progress → try one alternate direction from `steering` before replanning), STOP, or error.
- Run whenever moving in the field and more than ~30 units from an interaction point; drop run (walk) for the last stretch so we don't overshoot trigger triangles. Keep `--walk` to disable.
- Add a small `hold(buttons, seconds)` / `macro(sequence)` API on `Controller` for reusable button macros (e.g. `[('cross+up', 1.2), ('release', 0.1), ('circle', 0.12)]`), each step checking STOP and always releasing in `finally`.
- Log per-macro: duration held, % of time with Cross held, direction changes, why it ended.

## Phase 2 — checks and puzzles
- Decode (read-only) the treasure/item-pickup flags and field "!" check points the field scripts use, plus save points. Add an optional objective layer: on each field, visit unopened chests / pickups reachable on the walkmesh before taking the exit (flag `--collect`, default on). Verify each by the flag flipping; if it doesn't flip after 2 tries, skip and log.
- Fix the floor 65 Midgar model puzzle end-to-end: chests → carry part → model room slot (exact triangle + facing, confirm) → next part → Keycard 66 chest → floor 66. Add a regression test using `ram-model-room.bin` / `live-state.json` snapshots.
- Generic interaction helper: approach, stop running, face target, Circle, then handle the resulting dialogue.

## Phase 3 — later routes (data-driven)
- Move story steps out of if-chains into a route table (field_id, required flags/key items/progress → target type: gateway / actor / inspect / choice answer). Continue Shinra HQ: floor 66 (restroom vent + meeting eavesdrop), floor 67–68, jail sequence. Each step needs a verification flag so reloading a save resyncs. Stop with `blocked` on anything unverified — never guess.

## Phase 4 — combat strategy
- First validate `battle.decode_battle` in a real battle (log raw records, compare to on-screen HP) and record it in `verification.json`.
- Then add real command navigation using the menu cursor: Attack with target selection (lowest HP enemy), cure with Cure materia/Potion when any ally < 40% HP, Phoenix Down on KO, Limit Break when available, flee from random encounters only if health is critical, never flee bosses. Keep it behind `-BattleAssist` until live-validated.

## Testing (important)
- The user is using his PC while tests run: every test must run hidden/non-intrusively (no new windows, no focus changes). Use `Start-Laya.ps1` hidden mode and synthetic snapshots; unit tests with `.venv\Scripts\python.exe -m unittest -v`.
- Add tests: movement executor keeps Cross held across direction changes without intermediate release; Cross is released before any confirm; stall recovery; chest-visit ordering; Midgar puzzle state machine.
- Update `verification.json` only with things actually observed live.

Deliver in phases; after Phase 1 stop, run the unit tests, and report a live `decisions.jsonl` excerpt showing continuous held movement with `run_checks` ≈ `state_checks`.
