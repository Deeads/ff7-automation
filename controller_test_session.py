"""Keep a virtual pad attached; accept only bounded menu/cancel tests or stop."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import time
import vgamepad as vg

root = Path(__file__).resolve().parent
commands = root / "controller-test-command.json"
events = root / "controller-test-events.jsonl"
user32 = ctypes.WinDLL("user32")
user32.GetForegroundWindow.restype = wintypes.HWND
pad = vg.VX360Gamepad()
seen = None
if commands.exists():
    try:
        seen = json.loads(commands.read_text(encoding="utf-8-sig")).get("id")
    except (ValueError, OSError):
        pass

def record(event, **fields):
    line = json.dumps(dict(event=event, **fields))
    with events.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")
    print(line, flush=True)

try:
    pad.reset()
    pad.update()
    record("connected", timeout_seconds=900)
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        if commands.exists():
            try:
                command = json.loads(commands.read_text(encoding="utf-8-sig"))
            except (ValueError, OSError):
                time.sleep(0.1)
                continue
            token = command.get("id")
            if token is not None and token != seen:
                seen = token
                action = command.get("action")
                if action == "stop":
                    break
                button = {"menu": vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
                          "cancel": vg.XUSB_BUTTON.XUSB_GAMEPAD_A}.get(action)
                if button is None:
                    record("rejected", action=action)
                    continue
                foreground = user32.GetForegroundWindow()
                try:
                    pad.press_button(button=button)
                    pad.update()
                    time.sleep(0.35)
                finally:
                    pad.reset()
                    pad.update()
                record("button_released", action=action,
                       foreground_unchanged=user32.GetForegroundWindow() == foreground)
        time.sleep(0.1)
finally:
    pad.reset()
    pad.update()
    record("disconnected")
