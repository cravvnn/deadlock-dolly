"""Concrete stance pivots share the regular Follow transaction and restoration."""
import unittest
from dolly.follow_camera import BOUNDS, ENABLED, FOLLOW_AIM, PREFIX, FollowSettings, FollowTransaction

class FollowStance(unittest.TestCase):
    def rig(self):
        values = {PREFIX + n: float((lo + hi) / 2) for n, (lo, hi) in BOUNDS.items()}
        values.update({ENABLED: 0., FOLLOW_AIM: 0., PREFIX + 'fov': 90., PREFIX + 'ads_fov': 45.})
        for axis, standing, crouching in zip('xyz', (13., -11., 86.), (-7., 2., 55.)):
            values[PREFIX + 'pivot_' + axis + '_offset'] = standing
            values[PREFIX + 'pivot_' + axis + '_offset_crouching'] = crouching
        return values

    def test_same_pivot_on_update_and_exact_restore(self):
        values = self.rig(); original = dict(values)
        transaction = FollowTransaction(values.__getitem__, values.__setitem__)
        transaction.apply(FollowSettings(distance=205, shoulder=-17, height=12))
        for axis, expected in zip('xyz', (13., -11., 86.)):
            self.assertEqual(values[PREFIX + 'pivot_' + axis + '_offset'], expected)
            self.assertEqual(values[PREFIX + 'pivot_' + axis + '_offset_crouching'], expected)
        # A repeated Apply uses the current standing pivot, retaining the first snapshot.
        values[PREFIX + 'pivot_z_offset'] = 110.
        transaction.apply(FollowSettings(height=20))
        self.assertEqual(values[PREFIX + 'pivot_z_offset_crouching'], 110.)
        self.assertEqual(transaction.originals, original)
        for suffix in ('ads_x_offset', 'ads_y_offset', 'ads_z_offset', 'x_worst_case_offset', 'y_worst_case_offset', 'z_worst_case_offset'):
            self.assertEqual(values[PREFIX + suffix], original[PREFIX + suffix])
        transaction.restore()
        self.assertEqual(values, original)
        self.assertFalse(transaction.originals)

    def test_failed_stance_write_keeps_restore_retry(self):
        values = self.rig(); original = dict(values)
        broken = PREFIX + 'pivot_y_offset_crouching'; block = True; attempts = []
        def write(name, value):
            attempts.append((name, value))
            if block and name == broken:
                raise OSError('controlled stance write failure')
            values[name] = value
        transaction = FollowTransaction(values.__getitem__, write)
        with self.assertRaisesRegex(RuntimeError, 'original settings still need restoration'):
            transaction.apply(FollowSettings())
        self.assertIn(broken, [name for name, value in attempts])
        self.assertEqual(transaction.originals, original)
        self.assertFalse(transaction.restoring)  # Only true inside restore callbacks.
        self.assertEqual(values[ENABLED], 0)
        block = False
        transaction.restore()
        self.assertEqual(values, original)
        self.assertFalse(transaction.originals)
        self.assertFalse(transaction.restoring)

if __name__ == '__main__':
    unittest.main()
