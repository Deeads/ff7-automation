import copy
import struct
import unittest

from battle import decode_battle, battle_choices, OVERLAY_SIGNATURE
from ff7_state import fftext, choice_row
from navigation import center, contains, locate, route, waypoint, segment_distance, steering, approaches_wall, NoPath
from story import dialogue_action, target_action


def grid(cells):
    mesh = []
    for x,y in cells:
        a,b,c,d = [10*x,10*y,0], [10*x+10,10*y,0], [10*x,10*y+10,0], [10*x+10,10*y+10,0]
        for vertices in ([a,b,c],[b,d,c]):
            mesh.append(dict(id=len(mesh), vertices=vertices, neighbors=[-1]*3, locked=False))
    edges = {}
    for triangle in mesh:
        vertices = triangle['vertices']
        for i in range(3):
            edge = tuple(sorted((tuple(vertices[i]),tuple(vertices[(i+1)%3]))))
            if edge in edges:
                neighbor,j = edges[edge]
                triangle['neighbors'][i] = neighbor
                mesh[neighbor]['neighbors'][j] = triangle['id']
            else:
                edges[edge] = triangle['id'],i
    return mesh


class NavigationTests(unittest.TestCase):
    def test_diagonal_clears_floor_64_corner(self):
        mesh = [
            dict(id=0,vertices=[[868,170,-1],[869,108,-1],[770,171,-1]],neighbors=[-1,-1,1],locked=False),
            dict(id=1,vertices=[[871,247,-1],[868,170,-1],[770,171,-1]],neighbors=[2,0,-1],locked=False),
            dict(id=2,vertices=[[871,247,-1],[958,170,3],[868,170,-1]],neighbors=[-1,-1,1],locked=False)]
        state = dict(player=dict(position=[831.061,167.391,-1],triangle=0,collision_radius=34),actors=[],
            navigation=dict(mesh=mesh,direction_vectors=dict(up=[0,1],down=[0,-1],left=[-1,0],right=[1,0],up_right=[.707,.707])))
        self.assertEqual(steering(state,dict(position=center(mesh[2]),triangle=2))[0],'up_right')

    def test_wall_extension_does_not_block_corridor(self):
        self.assertFalse(approaches_wall([976.22,-144.483],[996.22,-144.483], [939,-179,0],[839,-180,0],34))

    def test_floor_63_stair_corridor_follows_wall_tangent(self):
        mesh = [
            dict(id=0,vertices=[[1014,-120,0],[939,-179,0],[932,-120,0]],neighbors=[1,-1,-1],locked=False),
            dict(id=1,vertices=[[939,-179,0],[1014,-120,0],[1014,-179,0]],neighbors=[0,2,-1],locked=False),
            dict(id=2,vertices=[[1196,-120,250],[1014,-179,0],[1014,-120,0]],neighbors=[3,1,-1],locked=False),
            dict(id=3,vertices=[[1014,-179,0],[1196,-120,250],[1196,-179,250]],neighbors=[2,-1,-1],locked=False)]
        state = dict(player=dict(position=[976.22,-144.483,0],triangle=0,collision_radius=34),actors=[],
            navigation=dict(mesh=mesh,direction_vectors=dict(up=[0,1],down=[0,-1],left=[-1,0],right=[1,0])))
        self.assertEqual(steering(state,dict(position=[1182,-150.5,230],triangle=3))[0],'right')

    def test_walks_around_hatt_at_floor_62_doorway(self):
        mesh = [
            dict(id=0, vertices=[[-687,-499,0],[-678,-322,0],[-491,-379,0]], neighbors=[1,-1,-1], locked=False),
            dict(id=1, vertices=[[-678,-322,0],[-687,-499,0],[-822,-337,0]], neighbors=[0,2,-1], locked=False),
            dict(id=2, vertices=[[-864,-499,0],[-822,-337,0],[-687,-499,0]], neighbors=[-1,1,-1], locked=False)]
        state = dict(player=dict(position=[-667.276,-403.545,0], triangle=0, collision_radius=34),
                     actors=[dict(position=[-722,-365,0], collision_radius=30, visible=True, solid=True)],
                     navigation=dict(mesh=mesh, direction_vectors=dict(left=[-1,0],right=[1,0],up=[0,1],down=[0,-1])))
        target = dict(position=center(mesh[2]), triangle=2)
        point,_ = waypoint(state,target)
        self.assertGreaterEqual(segment_distance([-722,-365],state['player']['position'],point),66)
        self.assertEqual(steering(state,target)[0], 'down')

    def test_routes_around_hole(self):
        mesh = grid([(0,0),(1,0),(2,0),(0,1),(2,1),(0,2),(1,2),(2,2)])
        start,goal = locate(mesh,[5,15]), locate(mesh,[25,15])
        path = route(mesh,start,goal)
        self.assertGreater(len(path),4)
        for i,j in zip(path,path[1:]):
            self.assertIn(j,mesh[i]['neighbors'])
        self.assertTrue(all(not (10<center(mesh[i])[0]<20 and 10<center(mesh[i])[1]<20) for i in path))

    def test_locked_goal_rejected(self):
        mesh=grid([(0,0)]);mesh[1]['locked']=True
        with self.assertRaises(NoPath): route(mesh,0,1)

    def test_disconnected_island_rejected(self):
        mesh=grid([(0,0),(2,2)])
        with self.assertRaises(NoPath): route(mesh,0,3)

    def test_multifloor_target_needs_height(self):
        mesh=grid([(0,0)]);upper=copy.deepcopy(mesh)
        for tri in upper:
            tri['id']+=2
            for v in tri['vertices']:v[2]=100
        mesh+=upper
        with self.assertRaises(NoPath): locate(mesh,[2,2])
        self.assertEqual(locate(mesh,[2,2,100]),2)

    def test_waypoint_pushes_across_portal(self):
        mesh=grid([(0,0)])
        state=dict(player=dict(position=[4.9,4.9,0],triangle=0),navigation=dict(mesh=mesh))
        point,path=waypoint(state,dict(position=[8,8,0],triangle=1))
        self.assertEqual(path,[0,1])
        self.assertTrue(contains(mesh[1],point))
        self.assertGreater(sum(point[:2]),10)


class DialogueTests(unittest.TestCase):
    def test_cursor_uses_sixteen_pixel_rows(self):
        self.assertEqual(choice_row(38),2)
        self.assertEqual(choice_row(54),3)
        self.assertIsNone(choice_row(0))
        self.assertIsNone(choice_row(39))
    def test_silent_answer_and_cursor(self):
        self.assertEqual(fftext(bytes([0xA9,0xA9,255])),'......')
        state=dict(field_id=241,dialogs=[dict(choice_pointer=True,text="Who are you?\nWhere's Aeris?\n......",choice_row=1)])
        self.assertEqual(dialogue_action(state)[0],'down')
        state['dialogs'][0]['choice_row']=2
        self.assertEqual(dialogue_action(state)[0],'confirm')

    def test_domino_answer_comes_from_save(self):
        state=dict(field_id=242,puzzles=dict(domino_password='KING'),dialogs=[dict(choice_pointer=True,
            text="What's the password?\nBEST\nKING\nORBS\nBOMB\nMAKO\nHOJO",choice_row=1)])
        self.assertEqual(dialogue_action(state)[0],'down')
        state['dialogs'][0]['choice_row']=2
        self.assertEqual(dialogue_action(state)[0],'confirm')

    def test_unknown_choice_waits(self):
        state=dict(field_id=242,puzzles=dict(domino_password='KING'),dialogs=[dict(choice_pointer=True,text='Buy something?',choice_row=0)])
        self.assertIsNone(dialogue_action(state)[0])


def battle_fixture():
    data=bytearray(0x200000);data[0xA0000:0xA0010]=OVERLAY_SIGNATURE
    struct.pack_into('<HH',data,0xF83BC,1,16)
    for slot,hp in ((0,500),(4,150)):
        at=0xF83E0+slot*0x68
        struct.pack_into('<HHII',data,at+0x28,50,100,hp,hp)
        struct.pack_into('<I',data,at+4,8)   # enabled (targetable), as seen live
    return data


class BattleTests(unittest.TestCase):
    def test_live_record_layout(self):
        battle=decode_battle(battle_fixture())
        self.assertEqual(battle['players'][0]['hp'],500)
        self.assertEqual(battle['enemies'][0]['hp'],150)

    def test_field_overlay_never_treated_as_battle(self):
        self.assertIsNone(decode_battle(bytes(0x200000)))

    def test_corrupt_hp_rejected(self):
        data=battle_fixture();struct.pack_into('<I',data,0xF840C,9000)
        self.assertIsNone(decode_battle(data))

    def test_experimental_input_requires_opt_in(self):
        self.assertEqual(set(battle_choices({'battle':decode_battle(battle_fixture())})[0]),{'wait'})

    def test_critical_health_attempts_escape(self):
        battle=decode_battle(battle_fixture());battle['players'][0]['hp']=10
        choices,_=battle_choices({'battle':battle},True)
        self.assertEqual(set(choices),{'wait','flee'})

    def test_inescapable_critical_battle_requests_strategy(self):
        battle=decode_battle(battle_fixture());battle['players'][0]['hp']=10;battle['escape_difficulty']=9
        # Waiting at critical HP in a battle that cannot be escaped only lets
        # enemies act (live game over, 2026-09-29): keep attacking instead.
        self.assertEqual(set(battle_choices({'battle':battle},True)[0]),{'wait','confirm'})

    def test_failed_escapes_fall_back_to_attack(self):
        battle=decode_battle(battle_fixture());battle['players'][0]['hp']=10
        self.assertEqual(set(battle_choices({'battle':battle,'flee_exhausted':True},True)[0]),{'wait','confirm'})


if __name__=='__main__': unittest.main()
