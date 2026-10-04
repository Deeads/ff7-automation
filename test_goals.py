"""Side goals: done from the live PHS mask; hand over on entering the goal's area."""
import json
import tempfile
import unittest
from pathlib import Path

import goals
import route

VINCENT = dict(id='vincent', label='Recruit Vincent Valentine', enabled=True,
               done_when=dict(phs_visible='Vincent'), area_prefixes=['sinbil', 'sinin'])


def field(fid, name, mask=0x0F):
    return dict(mode='field', field_id=fid, field_name=name, phs_visible=mask, story_progress=400)


class GoalTests(unittest.TestCase):
    def watch(self, *goal_list):
        tmp = Path(tempfile.mkdtemp())/'goals.json'
        tmp.write_text(json.dumps(dict(goals=list(goal_list))))
        return goals.GoalWatch(tmp)

    def test_done_reads_phs_bit6(self):
        self.assertFalse(goals.is_done(field(1, 'x', 0x0F), VINCENT))   # live mask: Cloud..Aeris
        self.assertTrue(goals.is_done(field(1, 'x', 0x4F), VINCENT))

    def test_entering_mansion_hands_over_once(self):
        w = self.watch(VINCENT)
        self.assertIsNone(w.check(field(100, 'nivl')))
        reason = w.check(field(101, 'sinin1_1'))
        self.assertIn('Recruit Vincent', reason)
        self.assertIsNone(w.check(field(101, 'sinin1_1')))          # same field: no repeat

    def test_starting_inside_does_not_stop(self):
        self.assertIsNone(self.watch(VINCENT).check(field(101, 'sinin1_1')))

    def test_done_or_disabled_goal_is_ignored(self):
        w = self.watch(VINCENT)
        w.check(field(100, 'nivl', 0x4F))
        self.assertIsNone(w.check(field(101, 'sinin1_1', 0x4F)))
        w = self.watch(dict(VINCENT, enabled=False))
        w.check(field(100, 'nivl'))
        self.assertIsNone(w.check(field(101, 'sinin1_1')))

    def test_goal_route_step_only_while_goal_active(self):
        step = dict(id='v1', field=101, progress=[0, 999], when=[], until=[], goal='vincent')
        old = goals._shared
        try:
            goals._shared = self.watch(VINCENT)
            self.assertTrue(route.applies(field(101, 'sinin1_1'), step))
            self.assertFalse(route.applies(field(101, 'sinin1_1', 0x4F), step))
        finally:
            goals._shared = old

    def test_shipped_goals_file_is_valid(self):
        self.assertEqual([g['id'] for g in goals.load()], ['vincent'])


if __name__ == '__main__':
    unittest.main()
