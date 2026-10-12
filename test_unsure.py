"""When the route has no effect: look around, re-enter contact lines, try unanswered choices."""
import unittest

from explore import Explorer, with_exploration, REENTER, MAX_YIELDS
from route import actor_near, beside_actor
from story import ChoiceGuesser, DialogNudge, ScreenNudge, target_action
from test_phase2 import grid_state

DOOR = dict(entity=7, name='ln0', line=[[400, 600, 0], [600, 600, 0]], scripts=[5], ok=False, jumps=False, asks=False)


def cell(player=(50, 58, 0), actors=()):
    s = grid_state([], list(actors), player=player)
    s.update(field_id=258, story_progress=293, key_items=[], gateways=[],
             field_triggers=dict(lines=[dict(DOOR)], talks=[]))
    return s


def asleep(entity=3, name='ti', position=(250, 250, 63)):
    # On a bed: her own triangle id is not part of the floor mesh.
    return dict(entity=entity, entity_name=name, position=list(position), triangle=9999, talkable=True, visible=True,
                solid=True, collision_radius=30, talk_radius=80, distance=10.0)


class Clock:
    t = 0.0
    def __call__(self): return self.t


class UnsureExplorationTests(unittest.TestCase):
    def test_door_lines_are_only_tried_when_unsure(self):
        self.assertEqual(Explorer().candidates(cell()), [])
        explorer = Explorer()
        explorer.unsure = True
        self.assertEqual([c[2] for c in explorer.candidates(cell())], [('line', 7), ('press', 7)])

    def test_nothing_left_widens_to_unsure_by_itself(self):
        target, reason = Explorer(clock=Clock()).target(cell(player=(50, 30, 0)))
        self.assertEqual(target['explore'], ('line', 7))

    def test_contact_line_is_left_and_walked_onto_again(self):
        explorer = Explorer(clock=Clock())
        explorer.unsure = True
        near = cell(player=(50, 58, 0))                      # 20 units from the line
        target, reason = explorer.target(near)
        self.assertIn('Step away', reason)
        self.assertGreaterEqual(abs(target['position'][1]-600), REENTER)
        far = cell(player=(50, 30, 0))
        target, reason = explorer.target(far)
        self.assertEqual((target['gateway'], target['line']), (-1, DOOR['line']))
        self.assertEqual(explorer.target(near)[0]['gateway'], -1)       # armed: walk in, do not back off again

    def test_a_trigger_that_talks_is_repeated_a_bounded_number_of_times(self):
        explorer = Explorer(clock=Clock())
        explorer.unsure = True
        for round_ in range(MAX_YIELDS):
            self.assertEqual(explorer.target(cell(player=(50, 30, 0)))[0]['explore'], ('line', 7))
            explorer.observe(dict(cell(), mode='dialog'))
            explorer.observe(cell())
        self.assertNotIn(('line', 7), [c[2] for c in explorer.candidates(cell()) if c[2] not in explorer.tried])

    def test_give_up_moves_to_the_next_candidate_at_once(self):
        explorer = Explorer(clock=Clock())
        explorer.unsure = True
        first = explorer.target(cell(player=(50, 30, 0)))[0]['explore']
        explorer.give_up(first)
        self.assertEqual(explorer.target(cell(player=(50, 30, 0)))[0]['explore'], ('press', 7))

    def test_route_demotion_switches_the_explorer_to_unsure(self):
        explorer = Explorer(clock=Clock())
        state = cell(player=(50, 30, 0))
        with_exploration(explorer, state, None, 'No verified story objective for field 258, progress 293.', unsure=True)
        self.assertTrue(explorer.unsure)


class ActorApproachTests(unittest.TestCase):
    def test_unnamed_model_is_inspected_not_treated_as_a_waypoint(self):
        guard = dict(entity=6, entity_name='', position=[540, 520, 0], triangle=0, talkable=True, visible=True,
                     solid=False, collision_radius=30, talk_radius=80, distance=45.0)
        state = cell(player=(50, 50, 0), actors=[guard])
        target = actor_near(state, guard, guard['entity_name'])
        self.assertTrue(target['inspect'])
        self.assertNotIn('actor', target)
        self.assertEqual(target_action(state, target)[0] in ('confirm', 'up', 'down', 'left', 'right'), True)
        self.assertTrue(target_action(state, target)[1].startswith('Interact'))

    def test_model_off_the_floor_is_approached_from_the_nearest_floor(self):
        sleeper = asleep()
        state = cell(player=(50, 50, 0), actors=[sleeper])
        target = beside_actor(state, sleeper)
        self.assertEqual((target['face'], target['inspect'], target['entity']), ([250, 250, 63], True, 3))
        self.assertLessEqual(abs(target['position'][0]-250)+abs(target['position'][1]-250), 160)
        explorer = Explorer()
        explorer.unsure = True
        self.assertIn(('poke', 3), [c[2] for c in explorer.candidates(state)])


class ChoiceGuesserTests(unittest.TestCase):
    TEXT = 'Cloud\n"..."\n        I wonder how Barret is doing\n        I wonder how Red XIII is doing'

    def ask(self, text=None, row=2, field=258):
        return dict(mode='dialog', field_id=field, story_progress=293, banks={},
                    dialogs=[dict(id=1, choice_pointer=True, text=text or self.TEXT, choice_row=row, ready=True, auto_close=False)])

    def test_options_start_at_the_cursor_and_rotate_between_visits(self):
        guesser = ChoiceGuesser()
        self.assertEqual(guesser.answer(self.ask()), 'I wonder how Barret is doing')
        self.assertEqual(guesser.answer(self.ask(row=3)), 'I wonder how Barret is doing')     # same dialogue: stable
        self.assertIsNone(guesser.answer(dict(mode='field', dialogs=[])))
        self.assertEqual(guesser.answer(self.ask()), 'I wonder how Red XIII is doing')
        guesser.answer(dict(mode='field', dialogs=[]))
        self.assertEqual(guesser.answer(self.ask()), 'I wonder how Barret is doing')            # round restarts

    def test_never_overrides_an_answer_already_decided(self):
        guesser = ChoiceGuesser()
        state = dict(self.ask(), dialogue_answer="Let's hurry on")
        self.assertIsNone(guesser.answer(state))
        self.assertIsNone(guesser.answer(self.ask(row=None)))


class DialogNudgeTests(unittest.TestCase):
    PROMPT = 'Access Menu with ? button'+'\n'+'End with the (START) button.'

    def say(self, text, **extra):
        return dict(mode='dialog', field_id=268, dialogs=[dict(dict(id=3, choice_pointer=False, text=text, choice_row=None,
                                                                 ready=True, auto_close=True), **extra)])

    def test_prompt_that_names_start_gets_start_after_a_wait(self):
        clock = Clock()
        nudge = DialogNudge(clock=clock)
        self.assertIsNone(nudge.action(self.say(self.PROMPT)))
        clock.t = 5
        self.assertIsNone(nudge.action(self.say(self.PROMPT)))
        clock.t = 7
        self.assertEqual(nudge.action(self.say(self.PROMPT)), 'start')
        clock.t = 8
        self.assertIsNone(nudge.action(self.say(self.PROMPT)))            # one press, then look again
        clock.t = 10
        self.assertEqual(nudge.action(self.say(self.PROMPT)), 'start')

    def test_silent_message_gets_the_usual_buttons_in_turn(self):
        clock = Clock()
        nudge = DialogNudge(clock=clock)
        pressed = []
        for clock.t in range(0, 20, 3):
            pressed.append(nudge.action(self.say('...')))
        self.assertEqual([p for p in pressed if p][:4], ['confirm', 'cancel', 'start', 'confirm'])

    def test_ordinary_dialogue_and_choices_are_left_alone(self):
        clock = Clock()
        nudge = DialogNudge(clock=clock)
        for state in (self.say('Hello', auto_close=False), self.say('Yes'+'\n'+'No', choice_pointer=True),
                      dict(mode='field', dialogs=[])):
            nudge.action(state)
            clock.t += 30
            self.assertIsNone(nudge.action(state))


class ScreenNudgeTests(unittest.TestCase):
    def test_unknown_screen_that_stays_gets_cross_a_bounded_number_of_times(self):
        clock = Clock()
        nudge = ScreenNudge(clock=clock)
        shop = dict(mode='unsupported', field_id=236, module_request=5, field_state=8)
        pressed = []
        for clock.t in range(0, 120, 2):
            pressed.append(nudge.action(shop))
        self.assertIsNone(pressed[9])                                   # 18 s: still waiting
        self.assertEqual(pressed[10], 'cancel')                         # 20 s
        self.assertEqual(pressed.count('cancel'), ScreenNudge.PRESSES)
        nudge.action(dict(mode='field', field_id=236))                  # back in play: a later screen starts over
        clock.t += 1
        nudge.action(shop)
        clock.t += 25
        self.assertEqual(nudge.action(shop), 'cancel')

    def test_bike_chase_swings_both_sides_without_a_limit(self):
        nudge = ScreenNudge(clock=Clock())
        bike = dict(mode='unsupported', field_id=103, field_state=12, module_request=6)
        self.assertEqual([nudge.action(bike) for _ in range(4)], ['square', 'confirm', 'square', 'confirm'])

    def test_party_screen_is_worked_until_the_save_shows_three_members(self):
        clock = Clock()
        nudge = ScreenNudge(clock=clock)
        screen = dict(mode='unsupported', field_id=170, field_state=7, module_request=5, party_slots=[0])
        pressed = []
        for _ in range(60):
            clock.t += 1
            pressed.append(nudge.action(screen))
        self.assertTrue(set(pressed) <= set(ScreenNudge.PARTY_KEYS))
        self.assertGreater(pressed.count('confirm'), 15)                # mostly Circle, on both panes
        self.assertTrue({'down', 'right'} <= set(pressed))
        clock.t += 0.1
        self.assertIsNone(nudge.action(screen))                         # paced
        screen['party_slots'] = [0, 1, 2]
        for _ in range(3):
            clock.t += 1
            self.assertEqual(nudge.action(screen), 'cancel')            # three in: only leave

    def test_party_screen_inputs_are_bounded(self):
        clock = Clock()
        nudge = ScreenNudge(clock=clock)
        screen = dict(mode='unsupported', field_id=170, field_state=7, module_request=5, party_slots=[0])
        count = 0
        for _ in range(ScreenNudge.PARTY_INPUTS+50):
            clock.t += 1
            count += nudge.action(screen) is not None
        self.assertEqual(count, ScreenNudge.PARTY_INPUTS)

    def test_supported_modes_are_never_touched(self):
        clock = Clock()
        nudge = ScreenNudge(clock=clock)
        for mode in ('field', 'dialog', 'battle', 'menu'):
            nudge.action(dict(mode=mode, field_id=1))
            clock.t += 60
            self.assertIsNone(nudge.action(dict(mode=mode, field_id=1)))


if __name__ == '__main__':
    unittest.main()
