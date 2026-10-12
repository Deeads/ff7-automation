import copy
import json
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import checkpoints as cp


def state(progress=100, **changes):
    return dict(game='FF7-USA-verified', emulator='duckstation', mode='field',
                story_progress=progress, field_id=10, field_name='test',
                party=[dict(name='Cloud',hp=200,max_hp=500)], **changes)


def save_bytes(serial='SCUS-94163'):
    data=bytearray(232)
    data[:4]=b'DUCC'
    data[136:136+len(serial)]=serial.encode()
    struct.pack_into('<III',data,204,16,1000,216)
    return bytes(data)


class MilestoneTests(unittest.TestCase):
    def test_attach_does_not_claim_past_quests(self):
        watch=cp.Milestones();watch.observe(state(1600),[])
        self.assertFalse(watch.pending)

    def test_increase_queued_once_and_rewind_clears_it(self):
        watch=cp.Milestones()
        for p in (100,101,101):watch.observe(state(p),[])
        self.assertEqual(list(watch.pending),['story-101'])
        watch.observe(state(80),[]);self.assertFalse(watch.pending)

    def test_side_goal_completion_without_progress_change(self):
        watch=cp.Milestones();defs=[dict(id='vincent',label='Recruit Vincent',done_when=dict(phs_visible='Vincent'))]
        watch.observe(state(phs_visible=0),defs)
        watch.observe(state(phs_visible=64),defs)
        self.assertEqual(watch.pending['goal-vincent']['label'],'Recruit Vincent')

    def test_unsupported_or_dead_party_never_saved(self):
        for changes in (dict(mode='battle'),dict(mode='dialog'),dict(fading=True),dict(movement_locked=True),dict(party=[dict(hp=0)])):
            s=state();s.update(changes);self.assertFalse(cp.safe_screen(s))


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.clock=[0]
        self.saver=Mock();self.saver.save.return_value=(save_bytes(),'SCUS-94163')
        self.manager=cp.Checkpoints(self.root,self.saver,lambda:self.clock[0]);self.pad=Mock()

    def tick(self,s):return self.manager.tick(s,self.pad)
    def trigger(self):
        self.tick(state());self.tick(state(101));self.clock[0]+=2

    def test_archive_metadata_dedupe_and_replay(self):
        self.trigger();self.assertTrue(self.tick(state(101)))
        index=cp.read_json(self.root/'checkpoints/index.json',[])
        self.assertEqual(len(index),1);self.assertEqual(index[0]['milestone_ids'],['story-101'])
        self.assertEqual((self.root/'checkpoints'/index[0]['file']).read_bytes(),save_bytes())
        self.manager.reset();self.tick(state(100));self.tick(state(101));self.clock[0]+=2;self.tick(state(101))
        self.assertEqual(self.saver.save.call_count,1)

    def test_battle_transition_defers_save(self):
        self.tick(state());s=state(101);s['mode']='battle';self.tick(s);self.clock[0]=5
        self.tick(s);self.saver.save.assert_not_called()
        self.tick(state(101));self.clock[0]+=2;self.tick(state(101));self.saver.save.assert_called_once()

    def test_error_does_not_mark_complete_and_retries(self):
        self.saver.save.side_effect=TimeoutError('no new file')
        self.trigger();self.tick(state(101));self.assertFalse((self.root/'checkpoints/index.json').exists())
        self.clock[0]+=1;self.tick(state(101));self.assertEqual(self.saver.save.call_count,1)
        self.clock[0]+=31;self.saver.save.side_effect=None;self.tick(state(101))
        self.assertEqual(cp.read_json(self.root/'checkpoint-status.json',{})['status'],'saved')

    def test_stop_never_calls_emulator(self):
        self.trigger();self.manager.tick(state(101),self.pad,lambda:True)
        self.saver.save.assert_not_called()

    def test_disabled_auto_still_allows_manual_and_no_duplicate_manual(self):
        cp.write_json(self.root/'checkpoint-config.json',dict(enabled=False))
        self.trigger();self.tick(state(101));self.saver.save.assert_not_called()
        cp.write_json(self.root/'checkpoint-request.json',dict(id='manual',time=time.time()))
        self.tick(state(101));self.clock[0]+=2;self.tick(state(101));self.tick(state(101))
        self.saver.save.assert_called_once()

    def test_observe_mode_never_saves(self):
        self.trigger();self.manager.tick(state(101),None);self.saver.save.assert_not_called()

    def test_expired_request_not_replayed_on_next_launch(self):
        cp.write_json(self.root/'checkpoint-request.json',dict(id='old',time=0))
        self.tick(state());self.clock[0]+=2;self.tick(state());self.saver.save.assert_not_called()


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=self.root/'duckstation';self.data.mkdir()
        self.saves=self.data/'savestates';self.saves.mkdir()
        (self.data/'settings.ini').write_text('[Hotkeys]\nSaveGlobalState10 = SDL-0/LeftStick\n')
        cp.write_json(self.root/'checkpoint-config.json',dict(duckstation_data=str(self.data)))
        self.clock=[0];self.adapter=cp.DuckStationSaver(self.root,lambda:self.clock[0],self.sleep)
    def sleep(self,secs):self.clock[0]+=secs

    def test_new_complete_file_required_and_old_slot_preserved(self):
        path=self.saves/'SCUS-94163_10.sav';old=save_bytes()+b'old';path.write_bytes(old)
        pad=Mock();pad.hold.side_effect=lambda *a:path.write_bytes(save_bytes())
        self.assertEqual(self.adapter.save(state(),pad,lambda:False)[0],save_bytes())
        self.assertEqual(next((self.root/'checkpoints/slot-backups').glob('*.sav')).read_bytes(),old)
        self.assertGreaterEqual(pad.release.call_count,2)

    def test_stale_file_times_out(self):
        (self.saves/'SCUS-94163_10.sav').write_bytes(save_bytes())
        with self.assertRaises(TimeoutError):self.adapter.save(state(),Mock(),lambda:False)

    def test_other_game_and_truncated_state_rejected(self):
        for data in (save_bytes('SLUS-00892'),save_bytes()[:-1],b'ram dump'):
            with self.assertRaises(ValueError):cp.validate_save(data)

    def test_conflicting_load_binding_prevents_input(self):
        with (self.data/'settings.ini').open('a') as f:f.write('LoadSelectedSaveState = SDL-0/LeftStick\n')
        pad=Mock()
        with self.assertRaises(ValueError):self.adapter.save(state(),pad,lambda:False)
        pad.hold.assert_not_called()

    def test_unsupported_emulator_never_presses_button(self):
        s=state();s['emulator']='retroarch';pad=Mock()
        with self.assertRaises(ValueError):self.adapter.save(s,pad,lambda:False)
        pad.hold.assert_not_called()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=self.root/'duckstation';self.data.mkdir()
        self.saves=self.data/'savestates';self.saves.mkdir()
        self.ini=self.data/'settings.ini'
        self.ini.write_text('[Hotkeys]\nLoadGlobalState10 = SDL-0/RightStick\nSaveGlobalState10 = SDL-0/LeftStick\n')
        cp.write_json(self.root/'checkpoint-config.json',dict(duckstation_data=str(self.data)))
        folder=self.root/'checkpoints';folder.mkdir()
        self.old,self.new=save_bytes()+b'',save_bytes()
        self.old=self.old[:-1]+bytes([self.old[-1]^1])
        (folder/'a.sav').write_bytes(self.old);(folder/'b.sav').write_bytes(self.new)
        cp.write_json(folder/'index.json',[dict(file='a.sav',label='Old'),dict(file='b.sav',label='New'),dict(file='gone.sav',label='Lost')])
        self.recovery=cp.Recovery(self.root)

    def test_newest_state_is_put_in_the_slot_and_loaded(self):
        slot=self.saves/'SCUS-94163_10.sav';slot.write_bytes(b'manual save')
        pad=Mock()
        self.assertEqual(self.recovery.restore(pad)['label'],'New')
        self.assertEqual(slot.read_bytes(),self.new)
        self.assertEqual(next((self.root/'checkpoints/slot-backups').glob('*.sav')).read_bytes(),b'manual save')
        pad.hold.assert_called_once()
        self.assertEqual(pad.hold.call_args[0][0],'restore')

    def test_repeated_game_overs_fall_back_to_an_older_state_then_start_over(self):
        labels=[self.recovery.restore(Mock())['label'] for _ in range(cp.RETRIES*2+1)]
        self.assertEqual(labels,['New']*cp.RETRIES+['Old']*cp.RETRIES+['New'])

    def test_no_load_binding_or_no_state_presses_nothing(self):
        self.ini.write_text('[Hotkeys]\nSaveGlobalState10 = SDL-0/LeftStick\n')
        pad=Mock()
        with self.assertRaises(ValueError):self.recovery.restore(pad)
        (self.root/'checkpoints/index.json').write_text('[]')
        with self.assertRaises(ValueError):self.recovery.restore(pad)
        pad.hold.assert_not_called()


class PanelTests(unittest.TestCase):
    def test_manual_save_queues_to_active_bot_without_second_controller(self):
        import laya_ui
        with tempfile.TemporaryDirectory() as folder, patch.object(laya_ui,'ROOT',Path(folder)), \
             patch.object(cp,'summary',return_value=dict(ready=True)), \
             patch.object(laya_ui,'bot_state',return_value=(True,'running',None)), \
             patch.object(laya_ui.subprocess,'Popen') as launch:
            code,body=laya_ui.checkpoint_action(dict(action='save'))
            self.assertEqual(code,202);launch.assert_not_called()
            self.assertTrue((Path(folder)/'checkpoint-request.json').exists())
            self.assertEqual(laya_ui.checkpoint_action(dict(action='save'))[0],409)

    def test_manual_save_without_bot_starts_save_worker_only(self):
        import laya_ui
        with tempfile.TemporaryDirectory() as folder, patch.object(laya_ui,'ROOT',Path(folder)), \
             patch.object(cp,'summary',return_value=dict(ready=True)), \
             patch.object(laya_ui,'bot_state',return_value=(False,'stopped',None)), \
             patch.object(laya_ui.subprocess,'Popen') as launch:
            self.assertEqual(laya_ui.checkpoint_action(dict(action='save'))[0],202)
            self.assertTrue(launch.call_args.args[0][1].endswith('checkpoints.py'))


if __name__=='__main__':unittest.main()
