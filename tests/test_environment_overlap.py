import math
import unittest
import numpy as np
from dolly.environment_panorama import FACES, capture_plan, panorama_rows

class OverlapTests(unittest.TestCase):
    def cube(self, size, function):
        q=((np.arange(size)+.5)/size*2-1)*math.tan(math.radians(50))
        result={}
        for f in FACES:
            r=np.array(f.forward)+q[None,:,None]*np.array(f.right)-q[:,None,None]*np.array(f.up)
            r/=np.linalg.norm(r,axis=-1,keepdims=True)
            result[f.name]=function(r).astype(np.float32)
        return result
    def project(self, images, **kw):
        return np.concatenate([b for _,b in panorama_rows(images,1024,face_fov=100,**kw)])
    def test_known_spherical_scene_all_directions(self):
        # Analytic radiance independently evaluated on the output sphere.
        fun=lambda r: (r+1)*4
        a=self.project(self.cube(256,fun))
        lon=(np.arange(1024)+.5)*2*math.pi/1024-math.pi
        lat=math.pi/2-(np.arange(512)+.5)*math.pi/512
        rays=np.stack(np.broadcast_arrays(np.cos(lat[:,None])*np.cos(lon),-np.cos(lat[:,None])*np.sin(lon),np.sin(lat[:,None])),axis=-1)
        self.assertLess(float(np.max(np.abs(a-fun(rays)))),.0003)
    def test_corrupt_outer_four_pixels_never_contribute(self):
        images=self.cube(128,lambda r:(r+1)*4)
        reference=self.project(images)
        for a in images.values():
            a[:4]=123456;a[-4:]=0;a[:,:4]=0;a[:,-4:]=123456
        np.testing.assert_array_equal(reference,self.project(images))
    def test_partition_of_unity_preserves_hdr_and_negative_values(self):
        images=self.cube(128,lambda r:np.broadcast_to([-.25,2,16],r.shape))
        a=self.project(images,block_rows=1)
        np.testing.assert_array_equal(a,np.broadcast_to([-.25,2,16],a.shape))
        np.testing.assert_array_equal(a,self.project(images,block_rows=32))
    def test_old_and_new_plans_are_distinct(self):
        old=capture_plan([0,0,0],1);new=capture_plan([0,0,0],1,face_fov=100)
        self.assertEqual(old['schema'],'dolly-environment-plan-v1')
        self.assertEqual(new['schema'],'dolly-environment-plan-v2')
        self.assertEqual(new['horizontal_fov'],100)
        self.assertEqual(new['vertical_fov'],100)
        for bad in [True,99,float('nan')]:
            with self.assertRaises(ValueError):capture_plan([0,0,0],1,face_fov=bad)

    def test_blend_continuity_at_edges_corners_and_support_boundary(self):
        from dolly.environment_panorama import _overlap_sample
        images={f.name:np.full((128,128,3),i+1,dtype=np.float32) for i,f in enumerate(FACES)}
        safe=math.tan(math.radians(50))*(1-9/128)
        for ray in ([1,1,0],[1,1,1],[1,safe,0]):
            rays=np.array([ray,ray],dtype=np.float64)
            rays[0,1]-=1e-7;rays[1,1]+=1e-7
            values=_overlap_sample(images,rays,128)
            self.assertLess(float(np.max(np.abs(values[0]-values[1]))),1e-4)
            self.assertTrue(np.all((values>=1)&(values<=6)))
