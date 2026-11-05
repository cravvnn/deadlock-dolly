import unittest

from dolly.follow_camera import (
    BOUNDS, ENABLED, FOLLOW_AIM, NAMES, PREFIX, FollowSettings,
    FollowTransaction, validate_value,
)


class FollowCameraTests(unittest.TestCase):
    def make_transaction(self):
        values = {PREFIX + name: float((low + high) / 2)
                  for name, (low, high) in BOUNDS.items()}
        values.update({ENABLED: 0., FOLLOW_AIM: 0.})
        original = dict(values)
        writes = []

        def write(name, value):
            writes.append((name, value))
            values[name] = value

        return FollowTransaction(values.__getitem__, write), values, original, writes

    def test_right_shoulder_and_behind_distance_use_negative_offsets(self):
        values = FollowSettings(distance=200, shoulder=45, height=30).values()
        self.assertEqual(values[PREFIX + 'x_offset'], -200)
        self.assertEqual(values[PREFIX + 'y_offset'], -45)
        self.assertEqual(values[PREFIX + 'z_offset'], 30)
        self.assertEqual(values[FOLLOW_AIM], 1)

    def test_invalid_inputs_fail_before_any_mutation(self):
        for kwargs in ({'distance': -1}, {'distance': 401}, {'shoulder': 151},
                       {'height': -151}, {'distance': float('nan')},
                       {'height': float('inf')}, {'shoulder': True}, {'distance': True}):
            tx, values, original, writes = self.make_transaction()
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                tx.apply(FollowSettings(**kwargs))
            self.assertEqual(values, original)
            self.assertFalse(writes)

    def test_unreviewed_and_nonboolean_controls_rejected(self):
        for name, value in (('sv_cheats', 1), (ENABLED, .5), (FOLLOW_AIM, 2)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_value(name, value)

    def test_snapshot_read_failure_performs_no_writes(self):
        tx, values, original, writes = self.make_transaction()
        del values[PREFIX + 'ads_fov']
        with self.assertRaises(KeyError):
            tx.apply(FollowSettings())
        self.assertFalse(writes)
        self.assertFalse(tx.originals)

    def test_updates_preserve_originals_and_separate_ads_values(self):
        tx, values, original, writes = self.make_transaction()
        tx.apply(FollowSettings())
        tx.apply(FollowSettings(distance=210, shoulder=-20, height=40))
        self.assertEqual(tx.originals, original)
        self.assertEqual(values[PREFIX + 'ads_x_offset'], original[PREFIX + 'ads_x_offset'])
        self.assertEqual(writes[-1], (ENABLED, 1))
        tx.restore()
        self.assertEqual(values, original)
        self.assertFalse(tx.originals)
        self.assertEqual(writes[-1], (ENABLED, original[ENABLED]))

    def test_clamping_rolls_back_complete_descriptor(self):
        tx, values, original, writes = self.make_transaction()
        write = tx.write

        def clamp(name, value):
            write(name, -100 if name == PREFIX + 'x_offset' and value == -200 else value)

        tx.write = clamp
        with self.assertRaisesRegex(RuntimeError, 'readback mismatch'):
            tx.apply(FollowSettings(distance=200))
        self.assertEqual(values, original)
        self.assertFalse(tx.originals)

    def test_restoration_failure_retains_snapshot_for_retry(self):
        tx, values, original, writes = self.make_transaction()
        tx.apply(FollowSettings())
        write = tx.write

        def fail(name, value):
            if name == PREFIX + 'ads_fov':
                raise OSError('connection lost')
            write(name, value)

        tx.write = fail
        with self.assertRaises(OSError):
            tx.restore()
        self.assertEqual(tx.originals, original)
        self.assertEqual(values[ENABLED], 0)
        tx.write = write
        tx.restore()
        self.assertEqual(values, original)
        self.assertFalse(tx.originals)

    def test_existing_enabled_rig_restores_enabled_last(self):
        tx, values, original, writes = self.make_transaction()
        values[ENABLED] = original[ENABLED] = 1
        tx.apply(FollowSettings())
        tx.restore()
        self.assertEqual(values, original)
        self.assertEqual(writes[-1], (ENABLED, 1))

    def test_weapon_zoom_is_applied_once_and_original_lens_restores(self):
        for normal_fov in (50, 90, 100, 120):
            with self.subTest(normal_fov=normal_fov):
                tx, values, original, writes = self.make_transaction()
                values[PREFIX + 'fov'] = original[PREFIX + 'fov'] = normal_fov
                values[PREFIX + 'ads_fov'] = original[PREFIX + 'ads_fov'] = 45
                tx.apply(FollowSettings())
                # The measured weapon subtracts 40 degrees. Keeping ADS45
                # makes that 5; a neutral lens preserves the stock zoom.
                self.assertEqual(values[PREFIX + 'ads_fov'] - 40, normal_fov - 40)
                self.assertEqual(values[PREFIX + 'fov'], normal_fov)
                self.assertEqual(values[PREFIX + 'ads_x_offset'], original[PREFIX + 'ads_x_offset'])
                self.assertLess(writes.index((PREFIX + 'ads_fov', normal_fov)), len(writes) - 1)
                tx.restore()
                self.assertEqual(values, original)

    def test_lens_update_uses_current_normal_and_retains_first_snapshot(self):
        tx, values, original, writes = self.make_transaction()
        tx.apply(FollowSettings())
        values[PREFIX + 'fov'] = 110
        tx.apply(FollowSettings(distance=210))
        self.assertEqual(values[PREFIX + 'ads_fov'], 110)
        self.assertEqual(tx.originals, original)
        tx.restore()
        self.assertEqual(values, original)

    def test_unsupported_neutral_lens_does_not_mutate_or_lose_originals(self):
        for active in (False, True):
            with self.subTest(active=active):
                tx, values, original, writes = self.make_transaction()
                if active:
                    tx.apply(FollowSettings())
                values[PREFIX + 'fov'] = 150
                before = dict(values)
                writes.clear()
                with self.assertRaisesRegex(ValueError, 'preserve weapon zoom'):
                    tx.apply(FollowSettings(distance=210))
                self.assertFalse(writes)
                self.assertEqual(values, before)
                self.assertEqual(tx.originals, original if active else {})

    def test_aimed_lens_readback_mismatch_restores_complete_descriptor(self):
        tx, values, original, writes = self.make_transaction()
        write = tx.write
        def clamp(name, value):
            write(name, 99 if name == PREFIX + 'ads_fov' and value == 100 else value)
        tx.write = clamp
        with self.assertRaisesRegex(RuntimeError, 'readback mismatch'):
            tx.apply(FollowSettings())
        self.assertEqual(values, original)
        self.assertFalse(tx.originals)

    def test_failed_apply_and_failed_rollback_remain_pending(self):
        tx, values, original, writes = self.make_transaction()
        write = tx.write

        def fail(name, value):
            if name in (PREFIX + 'x_offset', ENABLED):
                raise OSError('connection lost')
            write(name, value)

        tx.write = fail
        with self.assertRaisesRegex(RuntimeError, 'still need restoration'):
            tx.apply(FollowSettings())
        self.assertEqual(tx.originals, original)
        tx.write = write
        tx.restore()
        self.assertEqual(values, original)


if __name__ == '__main__':
    unittest.main()
