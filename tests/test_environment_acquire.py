import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from dolly.native_bridge import NativeBridge, MAPPING_BYTES
from dolly import environment_acquire as acquire
from dolly.environment_frame import HEADER
from dolly.environment_hdr import read_float_rgb_exr


class SharedFrame(io.BytesIO):
    def __getitem__(self,key):return self.getvalue()[key]


class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.memory=bytearray(MAPPING_BYTES)
        self.bridge=NativeBridge.create(mapping_factory=lambda name,size:self.memory,
            editor_pid=123,token='e'*32,start_heartbeat=False)
        self.bridge.bind_game(456);self.addCleanup(self.bridge.close)
        self.addCleanup(setattr,self.bridge,'status',self.bridge.status)
        self.view=dict(ack_command=7,tick=15300,paused=True,error=0,demo_name='test.dem')
        self.bridge.status=Mock(side_effect=lambda:dict(self.view))
        self.controller=SimpleNamespace(_require_connection=Mock(),_native_bridge=lambda:self.bridge,
                                        _require_native_demo=Mock())
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.output=Path(folder.name)/'frame.exr'
        self.now=0;self.transfer=None;self.response='good'
        self.pixels=np.array([[[8,-1,.5,1],[16,32,64,1]]],dtype='<f2')

    def mapping(self,name,size):
        self.assertTrue(name.startswith('Local\\DollyEnvironmentFrame-123-456-'))
        self.assertEqual(size,104)
        self.transfer=SharedFrame(bytes(size));return self.transfer

    def sleep(self,delay):
        self.now+=delay
        if self.response=='timeout':return
        request=acquire.REQUEST.unpack_from(self.memory,acquire.REQUEST_OFFSET)
        token=request[-1]
        if self.response=='stale':token=bytes(16)
        self.transfer.seek(0)
        self.transfer.write(HEADER.pack(b'DLYHDR01',1,88,token,2,1,16,1.25,2.5,acquire.REVIEWED_SHADER)+self.pixels.tobytes())
        self.transfer.seek(0)
        state=7 if self.response=='failed' else 4
        status=acquire.STATUS.pack(b'DLYHDRS1',2,1,request[3],state,104)
        self.memory[acquire.STATUS_OFFSET:acquire.STATUS_OFFSET+len(status)]=status
        if self.response=='changed':self.view['tick']+=1
        if self.response=='replaced':
            status=acquire.STATUS.pack(b'DLYHDRS1',2,1,request[3]+1,1,0)
            self.memory[acquire.STATUS_OFFSET:acquire.STATUS_OFFSET+len(status)]=status

    def run_capture(self,**kwargs):
        with patch('dolly.edition.is_environment',return_value=True):
            return acquire.capture_hdr_frame(self.controller,self.output,width=2,height=1,
                mapping_factory=self.mapping,clock=lambda:self.now,sleep=self.sleep,**kwargs)

    def test_complete_transfer_exports_exact_hdr_and_releases_mapping(self):
        before=bytes(self.memory)
        result=self.run_capture()
        np.testing.assert_array_equal(read_float_rgb_exr(self.output,2,1),self.pixels[:,:,:3])
        self.assertFalse(result['qualified_environment'])
        self.assertEqual(result['tick'],15300)
        self.assertTrue(self.transfer.closed)
        self.assertFalse(self.bridge._environment_capture_active)
        request=acquire.REQUEST.unpack_from(self.memory,acquire.REQUEST_OFFSET)
        self.assertEqual(request[4],0)
        self.assertEqual(bytes(self.memory[:acquire.REQUEST_OFFSET]),before[:acquire.REQUEST_OFFSET])

    def test_stale_camera_bad_token_failure_timeout_never_export(self):
        for response in ('stale','changed','failed','timeout'):
            with self.subTest(response=response):
                self.response=response;self.view['tick']=15300;self.now=0
                self.memory[acquire.STATUS_OFFSET:acquire.STATUS_OFFSET+acquire.STATUS.size]=bytes(acquire.STATUS.size)
                with self.assertRaises((ValueError,RuntimeError,TimeoutError)):self.run_capture()
                self.assertFalse(self.output.exists())
                self.assertTrue(self.transfer.closed)
                self.assertFalse(self.bridge._environment_capture_active)

    def test_cancellation_before_arm_has_no_request(self):
        before=bytes(self.memory)
        with self.assertRaises(InterruptedError):self.run_capture(cancelled=lambda:True)
        self.assertEqual(bytes(self.memory),before)
        self.assertIsNone(self.transfer)

    def test_replaced_command_is_not_cancelled_by_old_capture(self):
        self.response='replaced'
        with self.assertRaisesRegex(RuntimeError,'replaced'):self.run_capture()
        self.assertEqual(acquire.REQUEST.unpack_from(self.memory,acquire.REQUEST_OFFSET)[4],1)
        self.assertFalse(self.output.exists())

    def test_standard_edition_rejected_before_game_access(self):
        with patch('dolly.edition.is_environment',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'environment edition'):
                acquire.capture_hdr_frame(self.controller,self.output,width=2,height=1)
        self.controller._require_connection.assert_not_called()
