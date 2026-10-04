"""Bounded virtual-controller test. Never sends keys or activates a window."""
import argparse
import ctypes
from ctypes import wintypes
import json
import time
from pathlib import Path


class Gamepad(ctypes.Structure):
    _fields_ = [("buttons", wintypes.WORD), ("left_trigger", ctypes.c_ubyte),
                ("right_trigger", ctypes.c_ubyte), ("lx", ctypes.c_short),
                ("ly", ctypes.c_short), ("rx", ctypes.c_short), ("ry", ctypes.c_short)]


class State(ctypes.Structure):
    _fields_ = [("packet", wintypes.DWORD), ("gamepad", Gamepad)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--button-test", action="store_true",
                        help="Briefly press Xbox Y (normally PS Triangle when mapped).")
    parser.add_argument("--wait-for-trigger", type=Path,
                        help="Keep controller connected for up to ten minutes, until this file exists.")
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    foreground_before = user32.GetForegroundWindow()
    result = {"keyboard_events_sent": 0, "mouse_events_sent": 0,
              "window_activation_calls": 0, "button_test_requested": args.button_test}
    pad = None
    try:
        xinput = ctypes.WinDLL("xinput1_4")
        xinput.XInputGetState.argtypes = [wintypes.DWORD, ctypes.POINTER(State)]
        xinput.XInputGetState.restype = wintypes.DWORD
        def connected_slots():
            return {i for i in range(4) if
                    xinput.XInputGetState(i, ctypes.byref(State())) == 0}

        slots_before = connected_slots()
        import vgamepad as vg
        pad = vg.VX360Gamepad()
        pad.reset()
        pad.update()
        added = set()
        for _ in range(30):
            added = connected_slots() - slots_before
            if len(added) == 1:
                break
            time.sleep(0.1)
        if len(added) != 1:
            raise RuntimeError(f"Expected one new XInput slot; found {sorted(added)}")
        index = added.pop()

        def read_buttons():
            state = State()
            code = xinput.XInputGetState(index, ctypes.byref(state))
            if code:
                raise RuntimeError(f"XInputGetState({index}) returned {code}")
            return state.gamepad.buttons

        result.update(controller_created=True, controller_index=index,
                      neutral_before=read_buttons() == 0)
        if args.wait_for_trigger:
            print("Controller connected. Waiting for trigger file (10 minute limit).", flush=True)
            deadline = time.monotonic() + 600
            while not args.wait_for_trigger.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("No trigger received; controller released.")
                time.sleep(0.2)
            foreground_before = user32.GetForegroundWindow()
        if args.button_test:
            pad.press_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_Y)
            pad.update()
            time.sleep(0.2)
            result["y_press_observed_by_xinput"] = bool(read_buttons() & 0x8000)
            pad.reset()
            pad.update()
            time.sleep(0.2)
            result["neutral_after"] = read_buttons() == 0
        result["foreground_unchanged"] = user32.GetForegroundWindow() == foreground_before
        result["passed"] = result["neutral_before"] and (
            not args.button_test or (
                result["y_press_observed_by_xinput"] and result["neutral_after"]))
    except Exception as exc:
        result.update(passed=False, error=f"{type(exc).__name__}: {exc}")
    finally:
        if pad is not None:
            pad.reset()
            pad.update()
        print(json.dumps(result, indent=2))
        if args.result:
            args.result.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
