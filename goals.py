"""Optional side goals (e.g. recruit Vincent) layered on the story route.

goals.json lists goals. A goal is done when its verified save flag says so
(PHS visibility mask, savemap 0x10A6: bit0 Cloud ... bit6 Vincent; read live).
Route steps may carry "goal": "<id>"; they only apply while that goal is
enabled and not done (see story.objective). When the bot walks INTO a goal's
area and the goal has no verified steps for that field, it stops and hands
over ("YOUR TURN") instead of walking past it. Starting a run already inside
the area does not stop it, so you can decline by just pressing START again.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GOALS = ROOT/'goals.json'
PHS_NAMES = ['Cloud', 'Barret', 'Tifa', 'Aeris', 'Red XIII', 'Yuffie', 'Vincent', 'Cait Sith', 'Cid']


def load(path=GOALS):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8')).get('goals', [])
    except (OSError, ValueError):
        return []


def is_done(state, goal):
    when = goal.get('done_when') or {}
    if 'phs_visible' in when:
        name = when['phs_visible']
        mask = state.get('phs_visible')
        if mask is None or name not in PHS_NAMES:
            return False
        return bool(mask >> PHS_NAMES.index(name) & 1)
    return False


def active(state, goals):
    return [g for g in goals if g.get('enabled') and not is_done(state, g)]


def in_area(state, goal):
    name = (state.get('field_name') or '').lower()
    return any(name.startswith(p) for p in goal.get('area_prefixes', []))


class GoalWatch:
    def __init__(self, path=GOALS):
        self.path = Path(path)
        self.mtime = None
        self.goals = []
        self.last_field = None

    def refresh(self):
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self.goals, self.mtime = [], None
            return self.goals
        if mtime != self.mtime:
            self.goals, self.mtime = load(self.path), mtime
        return self.goals

    def check(self, state, route_has_goal_step=False):
        """Return a hand-over reason when we just entered an unfinished goal's area."""
        if state.get('mode') != 'field':
            return None
        field = state.get('field_id')
        entered = self.last_field is not None and field != self.last_field
        self.last_field = field
        if not entered or route_has_goal_step:
            return None
        for goal in active(state, self.refresh()):
            if in_area(state, goal):
                return (f'YOUR TURN: goal "{goal["label"]}" - you are at {goal.get("area_label", "its area")} '
                        f'and the bot has no verified steps for it yet. Do it, or press START to continue without it.')
        return None


def summary(state):
    """For the control panel: every goal with enabled/done."""
    return [dict(id=g['id'], label=g['label'], enabled=bool(g.get('enabled')),
                 done=is_done(state, g) if state else False, note=g.get('note', ''))
            for g in load()]


_shared = GoalWatch()


def active_ids(state):
    return {g['id'] for g in active(state, _shared.refresh())}
