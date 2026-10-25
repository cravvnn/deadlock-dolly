"""Rendered HDR reference backend using the verified native acquisition path.

World-class filtering removes animated units and effects broadly. It does not
prove every attachment/shadow is absent, nor physically calibrated illumination.
The reference schema stays separate from qualified empty-scene capture receipts.
"""
import hashlib
from pathlib import Path

from . import edition
from .convar_response import read_cvar_value
from .camera_commands import numeric
from .environment_acquire import capture_hdr_frame
from .environment_camera import position_face, verify_view, _pose
from .environment_probe import measure_source


class EnvironmentBackend:
    def __init__(self, controller, *, event=None, cancelled=lambda:False):
        if not edition.is_environment():raise RuntimeError('Use the environment edition for panorama capture')
        self.controller=controller;self.event=event;self.cancelled=cancelled
        self.bridge=controller._native_bridge();self.session=controller._session
        self.original=None;self.plan=None;self.changed=False
        if self.bridge is None or self.session is None:raise RuntimeError('Open a replay in the native editor first')

    def check(self, *, restoring=False):
        if not restoring and self.cancelled():
            from .environment_capture import CaptureCancelled
            raise CaptureCancelled('Panorama capture cancelled')
        c=self.controller;c._require_connection()
        if c._native_bridge() is not self.bridge or c._session is not self.session:
            raise RuntimeError('The panorama game session changed')
        status=self.bridge.status();c._require_native_demo(status,allow_idle=True)
        if not status.get('paused') or status.get('error'):raise RuntimeError('Pause the replay before capturing a panorama')
        if self.original and (status['tick']!=self.original['tick'] or status['demo_name']!=self.original['demo_name']):
            raise RuntimeError('The panorama replay changed')
        return status

    def view(self):
        state=self.check()
        editor=self.bridge.editor_status()
        # A reduced render scale (upscaling/FSR/DLSS or a manual viewport scale)
        # renders the scene below the source texture, so the HDR probe sees a
        # smaller viewport than the texture and cannot establish the source.
        # Force the verified full-resolution pass for the capture; the prior
        # scale is restored by restore()/Stop. Same reviewed lever standard
        # Dolly uses for depth export.
        self.controller.set_export_resolution()
        try:
            self.source=measure_source(self.bridge,self.check)
            width,height=self.source['width'],self.source['height']
            if width!=height or width not in (512,1024,2048,4096):
                raise RuntimeError('Panoramas need a square game view. Select a panorama render size and reopen the replay.')
            if self.event is not None:
                if (self.event.get('tick')!=state['tick'] or self.event.get('paused') is not True or
                    len(self.event.get('pose',[]))!=7):
                    raise RuntimeError('The capture shortcut belongs to an older replay view')
            sample=self.controller._sample_paused_native_view()
            current=self.check()
            if (current['tick']!=state['tick'] or current['demo_name']!=state['demo_name'] or
                    sample['tick']!=state['tick']):
                raise RuntimeError('The panorama replay changed while measuring its render size')
            if self.event is not None and any(abs(a-b)>1e-4 for a,b in zip(self.event.get('pose',[]),sample['pose'])):
                raise RuntimeError('The camera moved after the capture shortcut')
        except Exception:
            # Size discovery failed; do not leave the session at forced full
            # scale. The capture transaction releases it on success/failure.
            try:
                self.controller.clear_export_resolution()
            except Exception:
                pass
            raise
        self.original=dict(sample,demo_name=state['demo_name'],owner=editor.get('input_mode','panel'),face_size=width,
                           environment_face=getattr(self.bridge,'_mode',None)==5)
        return dict(position=sample['pose'][:3],tick=sample['tick'],face_size=width)

    def snapshot(self):
        if self.original is None:self.view()
        self.check()
        if self.controller._layer_hidden:
            raise RuntimeError('Restore the current layer filter before capturing a panorama')
        # Selected replay path belongs to the existing owned launch; no basename
        # search or replacement recording can silently supply its identity.
        name=self.original['demo_name']
        replay=Path(name)
        if not replay.is_file():raise RuntimeError('Cannot identify the loaded replay file')
        with replay.open('rb') as source:digest=hashlib.file_digest(source,'sha256').hexdigest()
        from ._runtime_generated import Preload
        self.identity=dict(replay_sha256=digest,game_build='client-sha256:'+Preload.CLIENT_SHA256)
        self.hud=read_cvar_value('citadel_hud_visible',self.controller._request('citadel_hud_visible'))
        self.had_hud_restore='citadel_hud_visible' in self.controller._playback_restore
        return dict(self.identity,original=self.original,hud=self.hud)

    def prepare(self, plan):
        self.check();self.plan=plan
        if plan['position']!=self.original['pose'][:3] or plan['tick']!=self.original['tick']:
            raise RuntimeError('Panorama plan no longer matches the original view')
        self.changed=True
        self.controller._playback_restore.setdefault('citadel_hud_visible',numeric(self.hud))
        self.controller._request('citadel_hud_visible 0')
        if read_cvar_value('citadel_hud_visible',self.controller._request('citadel_hud_visible'))!=0:
            raise RuntimeError('Game HUD did not hide')
        self.filter=self.controller.apply_layer_mode('world')
        self.check()
        # The general world layer keeps gameplay ziplines. An A/B/A HDR check
        # on build 6774 identifies their bright ribbons as ZipLineRopeSegment;
        # exclude them only for this environment workflow. Track the restore
        # obligation BEFORE sending the command, including partial failures.
        self.controller._layer_hidden=set(self.controller._layer_hidden or ()) | {'ZipLineRopeSegment'}
        self.controller._request('sc_setclassflags ZipLineRopeSegment 8')
        self.filter=dict(self.filter,hidden=sorted(set(self.filter['hidden']) | {'ZipLineRopeSegment'}),
                         environment_profile='world_without_ziplines')

    def capture(self, face, request_id, directory):
        self.check()
        before=position_face(self.controller,self.plan,face,cancelled=self.cancelled)
        # Let camera/temporal history settle; these frames are not recorded.
        self.controller._wait_paused_native_view(self.bridge,before['frame_count']+12,
                                                 expected_tick=self.plan['tick'])
        verify_view(self.controller,self.bridge,self.plan,_pose(self.plan,face),command=before['ack_command'])
        output=directory/(face['name']+'.exr')
        frame=capture_hdr_frame(self.controller,output,width=self.plan['face_size'],
                                height=self.plan['face_size'],cancelled=self.cancelled)
        after=verify_view(self.controller,self.bridge,self.plan,_pose(self.plan,face),command=before['ack_command'])
        if frame['camera_command']!=after['ack_command']:raise RuntimeError('HDR frame camera command changed')
        return dict(face,request_id=request_id,file=str(output),**self.identity,
                    width=frame['width'],height=frame['height'],horizontal_fov=self.plan['horizontal_fov'],vertical_fov=self.plan['vertical_fov'],
                    scene_empty=False,hud_hidden=True,scene_filter='world_classes',
                    filter_classes=self.filter,color_encoding='rendered_hdr_float',
                    exposure_signature=float(frame['exposure_view']).hex()+'/'+float(frame['exposure_post']).hex(),
                    scene_time=float(self.plan['tick']),scene_clock='paused_replay_tick',
                    acquisition=frame)

    def restore(self,snapshot):
        self.check(restoring=True)
        failures=[]
        # Always release the forced render scale, even if no scene changes were
        # made, so a failed capture cannot leave the session at full scale.
        try:
            self.controller.clear_export_resolution()
        except Exception as error:
            failures.append(str(error))
        if self.changed:
            try:self.controller.reset_layer_modes()
            except Exception as error:failures.append(str(error))
            try:
                self.controller._request('citadel_hud_visible '+numeric(snapshot['hud']))
                if read_cvar_value('citadel_hud_visible',self.controller._request('citadel_hud_visible'))!=snapshot['hud']:
                    raise RuntimeError('HUD restoration did not verify')
                if not self.had_hud_restore:self.controller._playback_restore.pop('citadel_hud_visible',None)
            except Exception as error:failures.append(str(error))
            try:
                options={'environment_face':True} if self.original.get('environment_face') else {}
                ack=self.bridge.start_flight(self.original['demo_name'],self.original['pose'],owner='panel',**options)
                self.controller._native_active=self.controller._native_manual=True
                self.controller._wait_paused_native_view(self.bridge,ack['frame_count'],expected_tick=self.original['tick'])
                actual=self.controller._sample_paused_native_view()
                if (any(abs(a-b)>1e-4 for a,b in zip(actual['pose'],self.original['pose'])) or
                    abs(actual['horizontal_fov']-self.original['horizontal_fov'])>1e-4):
                    raise RuntimeError('Original camera restoration did not verify')
                self.bridge.configure_editor(owner=self.original['owner'])
            except Exception as error:failures.append(str(error))
        if failures:raise RuntimeError('Panorama restoration: '+'; '.join(failures))
