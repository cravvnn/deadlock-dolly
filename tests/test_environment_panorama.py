import math
import json
import unittest
import numpy as np
from dolly.environment_panorama import FACES, capture_plan, panorama_rows


class PanoramaTests(unittest.TestCase):
    def test_plan_never_orbits_and_requires_measured_projection(self):
        plan = capture_plan([11, -22, 33], 123)
        self.assertEqual(len(plan['faces']), 6)
        for f in plan['faces']:
            self.assertEqual(f['position'], [11, -22, 33])
            self.assertEqual(f['tick'], 123)
        self.assertEqual(plan['horizontal_fov'], 90)
        self.assertEqual(plan['status'], 'planned_not_captured')
        self.assertEqual(json.loads(json.dumps(plan)), plan)

    def test_face_bases_match_source_angle_vectors(self):
        for f in FACES:
            p, y = math.radians(f.pitch), math.radians(f.yaw)
            np.testing.assert_allclose(f.forward, [math.cos(p)*math.cos(y), math.cos(p)*math.sin(y), -math.sin(p)], atol=1e-14)
            np.testing.assert_allclose(f.right, [math.sin(y), -math.cos(y), 0], atol=1e-14)
            np.testing.assert_allclose(f.up, [math.sin(p)*math.cos(y), math.sin(p)*math.sin(y), math.cos(p)], atol=1e-14)

    def test_direction_encoded_scene_round_trip_including_poles_and_seam(self):
        size = 128
        q = (np.arange(size)+.5)/size*2-1
        images = {}
        for f in FACES:
            rays = np.asarray(f.forward)+q[None, :, None]*np.asarray(f.right)-q[:, None, None]*np.asarray(f.up)
            rays /= np.linalg.norm(rays, axis=-1, keepdims=True)
            images[f.name] = (rays+1).astype(np.float32)*4  # HDR values exceed 1.
        actual = np.concatenate([block for _, block in panorama_rows(images, 512)])
        lon = ((np.arange(512)+.5)/512-.5)*2*math.pi
        lat = (.5-(np.arange(256)+.5)/256)*math.pi
        expected = np.stack(np.broadcast_arrays(np.cos(lat[:, None])*np.cos(lon),
            -np.cos(lat[:, None])*np.sin(lon), np.sin(lat[:, None])), axis=-1)
        self.assertLess(np.max(np.abs(actual-(expected+1)*4)), .015)
        self.assertGreater(actual.max(), 7.9)
        self.assertEqual(actual.dtype, np.float32)

    def test_invalid_faces_and_plan_rejected(self):
        good = {f.name: np.zeros((4, 4, 3), np.float32) for f in FACES}
        for bad in ({}, dict(good, front=np.zeros((3, 4, 3))), dict(good, front=np.full((4, 4, 3), np.nan))):
            with self.assertRaises(ValueError): list(panorama_rows(bad, 32))
        for kwargs in (dict(position=[0,0,float('nan')], tick=1), dict(position=[0,0,0], tick=True)):
            with self.assertRaises(ValueError): capture_plan(**kwargs)

    def test_bilinear_footprint_crosses_face_boundary(self):
        images = {f.name: np.zeros((8,8,3), np.float32) for f in FACES}
        images['right'][:] = 1
        actual = np.concatenate([block for _,block in panorama_rows(images,128)])
        # 45-degree longitude lies between pixels79 and80. Both footprints
        # straddle the edge; clamped sampling creates an abrupt zero->one step.
        before, after = actual[32,79,0], actual[32,80,0]
        self.assertGreater(before, 0)
        self.assertLess(before, .5)
        self.assertGreater(after, .5)
        self.assertLess(after, 1)
        self.assertLess(after-before,.5)

    def test_uint8_does_not_overflow_and_block_size_does_not_change_output(self):
        images = {f.name: np.full((4,4,3), 255, np.uint8) for f in FACES}
        a = np.concatenate([b for _,b in panorama_rows(images, 32, block_rows=1)])
        b = np.concatenate([b for _,b in panorama_rows(images, 32, block_rows=16)])
        np.testing.assert_array_equal(a, b)
        np.testing.assert_array_equal(a, np.full((16,32,3),255))
