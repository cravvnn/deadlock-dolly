import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from dolly.environment_hdr import write_float_rgb_exr, write_half_rgba_exr
from dolly.runtime import external_program_environment


class FloatRGBTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.exr'

    def test_rejects_incomplete_invalid_and_nonfinite_rows(self):
        for index, rows in enumerate(([], [np.zeros((2,3))]*3,
                                      [np.zeros((3,3))], [np.full((2,3),np.nan)],
                                      [np.full((2,3),1e300)])):
            path = self.path.with_name(f'bad{index}.exr')
            with self.assertRaises(ValueError): write_float_rgb_exr(path,2,2,rows)
            self.assertFalse(path.exists())

    def test_existing_output_and_partial_preserved(self):
        for path in (self.path,self.path.with_name(self.path.name+'.partial')):
            path.write_bytes(b'original')
            with self.assertRaises(FileExistsError):
                write_float_rgb_exr(self.path,1,1,[np.ones((1,3))])
            self.assertEqual(path.read_bytes(),b'original')
            path.unlink()

    def test_generator_error_never_publishes(self):
        def rows():
            yield np.zeros((1,3))
            raise RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            write_float_rgb_exr(self.path,1,2,rows())
        self.assertFalse(self.path.exists())

    def test_invalid_dimensions_rejected_before_output(self):
        for width,height in ((True,1),(0,1),(1,-1),(16385,1),(16384,16384)):
            with self.assertRaises(ValueError): write_float_rgb_exr(self.path,width,height,[])
        self.assertFalse(self.path.exists())

    def test_half_adapter_preserves_hdr_channels_and_row_order(self):
        pixels=np.array([[[8.9,-.5,0.125,np.nan],[2,4,8,0]],
                         [[16,32,64,1],[.25,.5,1,1]]],dtype='<f2')
        with patch('dolly.environment_hdr.write_float_rgb_exr') as writer:
            write_half_rgba_exr(self.path,2,2,pixels.tobytes())
            rows=list(writer.call_args.args[3])
        np.testing.assert_array_equal(np.asarray(rows,dtype='<f4'),pixels[:,:,:3].astype('<f4'))
        write_half_rgba_exr(self.path,2,2,pixels.tobytes())
        self.assertTrue(self.path.exists())

    def test_half_adapter_rejects_truncated_mutable_and_invalid_sizes(self):
        for width,height,data in ((1,1,b''),(1,1,bytes(9)),(1,1,bytearray(8)),
                                   (True,1,bytes(8)),(4097,1,b''),(1,0,b'')):
            with self.assertRaises(ValueError):write_half_rgba_exr(self.path,width,height,data)
        self.assertFalse(self.path.exists())

    def test_half_nonfinite_rgb_never_publishes(self):
        data=np.array([np.inf,1,2,1],dtype='<f2').tobytes()
        with self.assertRaises(ValueError):write_half_rgba_exr(self.path,1,1,data)
        self.assertFalse(self.path.exists())

    @unittest.skipUnless(shutil.which('ffmpeg'), 'Independent EXR reader requires FFmpeg')
    def test_ffmpeg_independently_decodes_rgb_float_without_clipping(self):
        # Distinct channels, rows, sign, subnormal and values above half-float
        # range expose channel/stride/orientation/quantization mistakes.
        pixels=np.array([[[.125,2,64],[-.5,65536,1e-20]],
                         [[1024,.25,4],[8,16,32]]],dtype=np.float32)
        write_float_rgb_exr(self.path,2,2,iter(pixels))
        with external_program_environment() as env:
            result=subprocess.run([shutil.which('ffmpeg'),'-v','error','-nostdin',
                '-i',str(self.path),'-frames:v','1','-f','rawvideo','-pix_fmt','gbrpf32le','pipe:1'],
                capture_output=True,timeout=20,env=env,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        decoded=np.frombuffer(result.stdout,dtype='<f4').reshape(3,2,2)
        np.testing.assert_array_equal(decoded,pixels[:,:,[1,2,0]].transpose(2,0,1))
        self.assertFalse(self.path.with_name(self.path.name+'.partial').exists())
