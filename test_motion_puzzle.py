import copy
import unittest
from unittest.mock import patch

from ff7_state import StateError
from laya_loop import Controller, choices_for
from motion import movement_command, follow_route
from story import midgar_model_objective


def field():
    return dict(mode='field',field_id=248,story_progress=264,key_items=[0]*8,puzzles=dict(midgar_placed=0,shinra_chests=[0,0]),
        player=dict(position=[100,100,0],triangle=0,collision_radius=0,facing=0),actors=[],dialogs=[],
        movement_locked=False,fading=False,navigation=dict(mesh=[dict(id=0,vertices=[[0,0,0],[1000,0,0],[0,1000,0]],neighbors=[-1,-1,-1],locked=False)],
        direction_vectors=dict(right=[1,0],left=[-1,0],up=[0,1],down=[0,-1]),direction_angles=dict(right=192,left=64,up=0,down=128)))


class MotionTests(unittest.TestCase):
    def test_run_modifier_on_long_field_movement(self):
        s=field();t=dict(field_id=248,position=[400,100,0],radius=12)
        self.assertEqual(movement_command(s,s,t)[:2],('right',True))
        self.assertEqual(movement_command(s,s,t,False)[:2],('right',False))

    def test_dialogue_or_puzzle_change_releases_run(self):
        s=field();t=dict(field_id=248,position=[400,100,0],radius=12)
        for changes in (dict(mode='dialog'),dict(field_id=249),dict(fading=True),dict(puzzles={'midgar_placed':64})):
            after=copy.deepcopy(s);after.update(changes)
            self.assertIsNone(movement_command(s,after,t)[0])

    def test_slow_approach_then_inspect_instead_of_skipping(self):
        s=field();t=dict(field_id=248,position=[180,100,0],radius=12,inspect=True,face=[200,100])
        # Runs while far away, walks the last WALK_MARGIN units before the trigger.
        self.assertEqual(movement_command(s,s,t)[:2],('right',True))
        s['player']['position']=[140,100,0]
        self.assertEqual(movement_command(s,s,t)[:2],('right',False))
        s['player'].update(position=[175,100,0],facing=192)
        self.assertIsNone(movement_command(s,s,t)[0])
        self.assertIn('confirm',choices_for(s,t)[0])

    def test_combined_direction_and_cross_single_report(self):
        events=[]
        class Pad:
            def reset(self):events.append('reset')
            def press_button(self,button):events.append(button)
            def update(self):events.append('update')
        c=Controller.__new__(Controller);c.pad=Pad();c.buttons=dict(up=1,cancel=2)
        c.set_movement('up',True)
        self.assertEqual(events,['reset',3,'update'])
        with self.assertRaises(ValueError):c.set_movement('confirm',True)

    def test_read_failure_releases_held_buttons(self):
        s=field();t=dict(field_id=248,position=[400,100,0],radius=12)
        class Source:
            count=0
            def snapshot(self,attempts):
                assert attempts==1
                self.count+=1
                if self.count>1:raise StateError('Loading')
                return s
        class Pad:
            user32=type('Window',(),{'GetForegroundWindow':lambda self:12})()
            released=False
            def set_movement(self,action,running):self.last=(action,running)
            def release(self):self.released=True
        pad=Pad();r=follow_route(pad,Source(),s,t,lambda:False,duration=.2)
        self.assertTrue(pad.released);self.assertTrue(r['sent']);self.assertEqual(pad.last,('right',True))

    def test_stop_never_starts_macro(self):
        s=field();t=dict(field_id=248,position=[400,100,0],radius=12)
        class Pad:
            user32=type('Window',(),{'GetForegroundWindow':lambda self:12})()
            released=False
            def release(self):self.released=True
        pad=Pad();r=follow_route(pad,None,s,t,lambda:True)
        self.assertFalse(r['sent']);self.assertTrue(pad.released)


class PuzzleTests(unittest.TestCase):
    def test_first_chest_and_next_unlocked_chest(self):
        s=field()
        with patch('story.actor_target',side_effect=lambda state,name:{'actor':name}):
            self.assertEqual(midgar_model_objective(s)[0]['actor'],'PARTD')
            s['puzzles'].update(midgar_placed=64,shinra_chests=[0,2])
            self.assertEqual(midgar_model_objective(s)[0]['actor'],'PARTB')

    def test_carried_piece_goes_to_model_not_another_chest(self):
        s=field();s['key_items'][4]=64
        with patch('story.gateway_target',side_effect=lambda state,dest:{'destination':dest}):
            self.assertEqual(midgar_model_objective(s)[0]['destination'],249)
        s['field_id']=249
        target,_=midgar_model_objective(s)
        self.assertTrue(target['inspect']);self.assertEqual(target['triangle'],70)

    def test_completed_model_collects_keycard_then_exits(self):
        s=field();s['puzzles']['midgar_placed']=248
        with patch('story.actor_target',side_effect=lambda state,name:{'actor':name}):
            self.assertEqual(midgar_model_objective(s)[0]['actor'],'TAKARA')
        s['key_items'][4]=2
        with patch('story.gateway_target',side_effect=lambda state,dest:{'destination':dest}):
            self.assertEqual(midgar_model_objective(s)[0]['destination'],250)


if __name__=='__main__':unittest.main()
