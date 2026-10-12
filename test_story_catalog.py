import json
from pathlib import Path
import tempfile
import unittest

from story_catalog import StoryCatalog, condition_matches, applies, target_for
from route import RouteError


def state(field=700, progress=1500):
    return dict(mode='field', field_id=field, field_name='latefield', story_progress=progress,
                key_items=[], banks={'1': bytes([3, 8, 0] + [0]*253).hex()}, party=[{'id': 2}],
                player=dict(position=[0, 0, 0], triangle=0, collision_radius=10),
                actors=[], gateways=[dict(id=0, destination=701, line=[[20, 0, 0], [30, 0, 0]])],
                navigation=dict(mesh=[dict(id=0, vertices=[[-100, -100, 0], [100, -100, 0], [0, 100, 0]],
                                          neighbors=[-1, -1, -1], locked=False)],
                                direction_vectors={'up': [0, 1], 'right': [1, 0]}))


def row(**changes):
    return dict(dict(fieldId=700, sourceFieldName='latefield', sourceEntityName='gateway0', kind='Location',
                     sourceScriptType='Gateway', x=25, y=0, z=0, minimumGameMoment=1499,
                     maximumGameMoment=1501, targetGameMoment=1502, label='Continue through this exit',
                     priority=0), **changes)


class ConditionTests(unittest.TestCase):
    def test_flags_bit_counts_numbers_party_members(self):
        s = state()
        self.assertTrue(condition_matches(s, dict(bank=1, address=0, mask=3, value=3)))
        self.assertTrue(condition_matches(s, dict(bank=1, address=0, mask=3, minimumSetBits=2)))
        self.assertFalse(condition_matches(s, dict(bank=1, address=1, mask=255, minimumSetBits=2)))
        self.assertTrue(condition_matches(s, dict(bank=1, address=1, mask=255, minimumValue=7, maximumValue=9)))
        self.assertTrue(condition_matches(s, dict(partyMemberId=2, requirePartyMember=True)))
        self.assertFalse(condition_matches(s, dict(partyMemberId=2, requirePartyMember=False)))
        with self.assertRaises(RouteError):
            condition_matches(s, dict(bank=99, address=0, mask=255, value=0))
        with self.assertRaises(RouteError):
            condition_matches(s, dict(unknown=True))

    def test_stale_field_and_unavailable_runtime_conditions_rejected(self):
        self.assertTrue(applies(state(), row()))
        self.assertFalse(applies(state(), row(sourceFieldName='different-build')))
        self.assertFalse(applies(state(), row(requiredEnabledLineEntityId=1)))
        self.assertFalse(applies(state(), row(requiredPlayerTriangles=[1])))
        self.assertFalse(applies(state(), row(manualNavigationGuidance='Requires a minigame')))
        self.assertFalse(applies(state(progress=1502), row()))

    def test_gateway_on_different_height_is_rejected(self):
        self.assertIsNone(target_for(state(), row(z=1000)))


class CatalogTests(unittest.TestCase):
    def test_late_story_route_and_bounded_attempt(self):
        now = [0]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'catalog.json'
            path.write_text(json.dumps(dict(schemaVersion=1, definitions=[row()])))
            catalog = StoryCatalog(path, clock=lambda: now[0])
            target, reason = catalog.target(state())
            self.assertEqual(target['gateway'], 0)
            self.assertIn('Catalogue', reason)
            now[0] = 16
            self.assertIsNone(catalog.target(state())[0])
            self.assertIsNotNone(catalog.target(state(progress=1501))[0])

    def test_bad_schema_or_missing_file_is_optional(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'catalog.json'
            catalog = StoryCatalog(path)
            self.assertEqual(catalog.target(state()), (None, None))
            path.write_text('{broken')
            self.assertEqual(catalog.target(state()), (None, None))
            self.assertIsNotNone(catalog.error)

    def test_unknown_condition_never_becomes_an_action(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'catalog.json'
            path.write_text(json.dumps(dict(schemaVersion=1, definitions=[row(requiredCondition={'newFlag': 1})])))
            self.assertIsNone(StoryCatalog(path).target(state())[0])


if __name__ == '__main__':
    unittest.main()
