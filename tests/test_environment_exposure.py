import threading
import unittest
from dolly.environment_exposure import CONTROLS, DIAGNOSTIC_CONTROLS, trial_exposure, inspect_exposure
from dolly.environment_capture import CaptureCancelled


class Controller:
    def __init__(self):
        self.values=dict(zip(CONTROLS,(0.,-1.,-1.)))
        self._look_restore={}
        self._op_lock=threading.RLock()
        self._session=object()
        self.commands=[]
        self.clamp=False
        self.unreadable=None
        self.fail_restore=False
    def _require_connection(self): pass
    def _require_quiet_session(self, label): pass
    def _request(self, command, **kwargs):
        self.commands.append(command)
        parts=command.split()
        name=parts[0]
        if len(parts)>1:
            value=float(parts[1])
            if self.fail_restore and name==CONTROLS[0] and value==0:
                raise RuntimeError('connection lost')
            self.values[name]=.5 if self.clamp and name==CONTROLS[0] and value==1 else value
            return ''
        return 'unreadable' if name==self.unreadable else f'{name} = {self.values[name]:.17g}'


class ExposureTests(unittest.TestCase):
    def test_inspection_only_queries_and_preserves_unknown_response(self):
        c=Controller(); c.values.update(mat_fullbright=0,mat_tonemap_debug=0)
        c.unreadable='mat_fullbright'
        result=inspect_exposure(c)
        self.assertEqual(c.commands,list(DIAGNOSTIC_CONTROLS))
        self.assertEqual(result['controls']['mat_fullbright'],
                         dict(value=None,response='unreadable',readable=False))
        self.assertEqual(result['controls']['mat_tonemap_debug']['value'],0)
        self.assertFalse(result['pixel_exposure_verified'])
        self.assertFalse(c._look_restore)

    def test_inspection_cancellation_prevents_next_query(self):
        c=Controller()
        with self.assertRaises(CaptureCancelled):
            inspect_exposure(c,cancelled=lambda:bool(c.commands))
        self.assertEqual(c.commands,[CONTROLS[0]])

    def test_inspection_session_change_rejects_result(self):
        c=Controller(); original=c._request
        def request(*args,**kwargs):
            result=original(*args,**kwargs); c._session=object(); return result
        c._request=request
        with self.assertRaisesRegex(RuntimeError,'session changed'):
            inspect_exposure(c)
        self.assertEqual(c.commands,[CONTROLS[0]])

    def test_inspection_requires_session(self):
        c=Controller(); c._session=None
        with self.assertRaisesRegex(RuntimeError,'owned game session'):
            inspect_exposure(c)
        self.assertFalse(c.commands)

    def test_inspection_transport_failure_is_not_unknown_value(self):
        c=Controller()
        def request(*args,**kwargs): raise OSError('disconnected')
        c._request=request
        with self.assertRaises(OSError): inspect_exposure(c)

    def test_reads_before_writes_and_restores_exact_saved_values(self):
        c=Controller()
        c.values[CONTROLS[1]]=.125
        original=dict(c.values)
        with trial_exposure(c) as trial:
            self.assertEqual(c.commands[:3],list(CONTROLS))
            self.assertEqual(c._look_restore,original)
            self.assertEqual(c.values,dict(zip(CONTROLS,(1,-1,-1))))
            self.assertFalse(trial['pixel_exposure_verified'])
        self.assertEqual(c.values,original)
        self.assertFalse(c._look_restore)

    def test_unreadable_snapshot_prevents_all_writes(self):
        c=Controller(); c.unreadable=CONTROLS[2]
        with self.assertRaises(ValueError):
            with trial_exposure(c): self.fail('entered body')
        self.assertTrue(all(' ' not in command for command in c.commands))
        self.assertFalse(c._look_restore)

    def test_rejected_override_is_rolled_back_before_body(self):
        c=Controller(); c.clamp=True
        with self.assertRaisesRegex(RuntimeError,'clamped'):
            with trial_exposure(c): self.fail('entered body')
        self.assertEqual(c.values[CONTROLS[0]],0)
        self.assertFalse(c._look_restore)

    def test_body_failure_restores(self):
        c=Controller()
        with self.assertRaisesRegex(ValueError,'capture failed'):
            with trial_exposure(c): raise ValueError('capture failed')
        self.assertEqual(c.values[CONTROLS[0]],0)
        self.assertFalse(c._look_restore)

    def test_drift_during_trial_rejected_and_original_restored(self):
        c=Controller()
        with self.assertRaisesRegex(RuntimeError,'changed during'):
            with trial_exposure(c): c.values[CONTROLS[0]]=2
        self.assertEqual(c.values[CONTROLS[0]],0)
        self.assertFalse(c._look_restore)

    def test_cancel_during_apply_stops_new_changes_but_restores(self):
        c=Controller()
        with self.assertRaises(CaptureCancelled):
            with trial_exposure(c,cancelled=lambda: any(' ' in cmd for cmd in c.commands)):
                self.fail('entered body')
        writes=[cmd for cmd in c.commands if ' ' in cmd]
        self.assertEqual(writes,[CONTROLS[0]+' 1',CONTROLS[0]+' 0',CONTROLS[1]+' -1',CONTROLS[2]+' -1'])
        self.assertFalse(c._look_restore)

    def test_restore_failure_retains_pending_original(self):
        c=Controller()
        with self.assertRaisesRegex(RuntimeError,'restoration incomplete'):
            with trial_exposure(c): c.fail_restore=True
        self.assertEqual(c._look_restore,{CONTROLS[0]:0})

    def test_pending_original_not_overwritten(self):
        c=Controller(); c._look_restore[CONTROLS[0]]=.5
        with self.assertRaisesRegex(RuntimeError,'pending'):
            with trial_exposure(c): self.fail('entered body')
        self.assertFalse(c.commands)
        self.assertEqual(c._look_restore[CONTROLS[0]],.5)

    def test_changed_session_never_receives_restore_commands(self):
        c=Controller()
        with self.assertRaisesRegex(RuntimeError,'session changed'):
            with trial_exposure(c):
                count=len(c.commands)
                c._session=object()
        self.assertEqual(len(c.commands),count)
        self.assertEqual(len(c._look_restore),3)

    def test_invalid_scale_prevents_all_access(self):
        for value in (float('nan'),0,-1,True,9):
            c=Controller()
            with self.assertRaises(ValueError):
                with trial_exposure(c,value): self.fail('entered body')
            self.assertFalse(c.commands)


if __name__=='__main__': unittest.main()
