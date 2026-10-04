"""Discover battle-menu cursor bytes in a live battle by RAM differencing.

Inputs are only harmless menu moves: Down/Up in the command list, one Circle
into targeting, Left/Right there, then Cross twice to back out without acting.
Bytes that also change with no input (timers, animation) are excluded.
Result: battle-menu-probe.json with candidate addresses for battle_menu.json.
Reads RAM only; the pad is released in `finally` and STOP is honored.
"""
import json
import time

import numpy

from ff7_state import RAM_SIZE
from ram_probe import read

SETTLE = 0.25


def ram(source):
    return numpy.frombuffer(read(source.handle, source.base, RAM_SIZE), numpy.uint8)


def noise_mask(source, samples=8, gap=0.06):
    first = ram(source)
    changed = numpy.zeros(RAM_SIZE, bool)
    for _ in range(samples):
        time.sleep(gap)
        changed |= ram(source) != first
    return changed


def toggles(source, pad, forward, back, trials, stopped):
    """Bytes that change on `forward` and return on `back`, every trial."""
    consistent = None
    values = []
    for _ in range(trials):
        if stopped():
            break
        before = ram(source)
        pad.hold(forward, 0.08, stopped); time.sleep(SETTLE)
        moved = ram(source)
        pad.hold(back, 0.08, stopped); time.sleep(SETTLE)
        after = ram(source)
        hit = (moved != before) & (after == before)
        consistent = hit if consistent is None else consistent & hit
        values.append((before, moved))
    if consistent is None:
        return []
    out = []
    for at in numpy.flatnonzero(consistent)[:64]:
        out.append(dict(address=int(at), hex=f'0x{int(at):06x}',
                        values=[[int(b[at]), int(m[at])] for b, m in values]))
    return out


def probe(source, pad, stopped, trials=3):
    report = dict(time=time.time(), note='Candidates only: confirm against a screenshot before marking battle_menu.json verified.')
    try:
        noise = noise_mask(source)
        report['noise_bytes'] = int(noise.sum())
        report['command_cursor'] = [c for c in toggles(source, pad, 'down', 'up', trials, stopped)
                                    if not noise[c['address']]]
        if stopped():
            return report
        pad.perform('confirm', stopped=stopped); time.sleep(SETTLE)   # into targeting (or a submenu)
        report['target_cursor'] = [c for c in toggles(source, pad, 'right', 'left', trials, stopped)
                                   if not noise[c['address']]]
    finally:
        # Back out without committing anything; Cross = cancel in battle menus.
        for _ in range(2):
            if not stopped():
                pad.perform('cancel', stopped=stopped)
        pad.release()
    return report


def save(report, path):
    with open(path, 'w', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
