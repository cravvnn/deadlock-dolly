"""Ten reviewed failure boundaries; real owners with local OS/Tk fixtures.

No game or external service. These are the distinct regression owners missing
from the core portfolio; exhaustive comparison/benchmark probes stay private.
"""
from pathlib import Path
import contextlib,copy,gc,json,mmap,struct,tempfile,threading,time,weakref
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
import unittest

def raises(kind,call):
    try:call()
    except BaseException as exc:
        assert isinstance(exc,kind),f'expected {kind.__name__}, got {type(exc).__name__}: {exc}'
        return exc
    raise AssertionError('expected '+kind.__name__)

def p1():
    from dolly import native_bridge as m,media_wire,visualization_wire,visualization_preview_wire
    class Mapping(bytearray):
        closes=0
        def close(self):self.closes+=1
    for fault in ('media','graphics','input'):
        bridge=m.NativeBridge.create(mapping_factory=lambda name,size:Mapping(size),start_heartbeat=True)
        primary=OSError('final diagnostic refused')
        bridge._viewer_mapping=Mapping(visualization_wire.MAPPING_BYTES)
        bridge._preview_mapping=Mapping(visualization_preview_wire.MAPPING_BYTES)
        maps=[bridge._mapping,bridge._viewer_mapping,bridge._preview_mapping]
        memory=view=None
        try:
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(bridge,'release',return_value=None))
                if fault=='media':
                    memory=mmap.mmap(-1,media_wire.MAPPING_BYTES)
                    view=memoryview(memory);bridge._media_mapping=memory
                    expected=BufferError
                else:
                    stack.enter_context(patch.object(bridge,'_sample_'+fault,side_effect=primary))
                    expected=OSError
                error=raises(expected,bridge.close)
                if fault!='media':assert error is primary
                assert bridge._heartbeat_stop.is_set(),'close failure left heartbeat running'
                assert bridge._closed and all(p.closes==1 for p in maps),'close failure stranded other mappings'
                assert bridge._thread is None or not bridge._thread.is_alive()
                bridge.close();assert all(p.closes==1 for p in maps),'second close repeated unmapping'
                if view is not None:assert not memory.closed,'held media view should remain unresolved'
        finally:
            bridge._heartbeat_stop.set()
            if bridge._thread:bridge._thread.join(timeout=2)
            if view is not None:view.release();memory.close()
            if not bridge._closed:
                with patch.object(bridge,'release',return_value=None):bridge.close()

def p2():
    from dolly import native_bridge as m,media_wire
    holder=[]
    def factory(name,size):
        value=mmap.mmap(-1,size,access=mmap.ACCESS_READ);holder.append(value);return value
    bridge=m.NativeBridge.create(mapping_factory=lambda n,s:bytearray(s),start_heartbeat=False)
    try:
        bridge._mapping_factory=factory
        error=raises(TypeError,bridge._ensure_media)
        assert holder[-1].closed,'provisional media mapping retained by failure traceback'
        assert bridge._media_mapping is None
        # Keep error/traceback alive above while checking explicit closure.
        assert error.__traceback__ is not None
        def writable(name,size):
            value=mmap.mmap(-1,size);holder.append(value);return value
        bridge._mapping_factory=writable;bridge._ensure_media()
        assert bridge._media_mapping is holder[-1] and not holder[-1].closed
        assert bytes(holder[-1][:16])==bytes(16)
        # Subsequent calls reuse the admitted object.
        count=len(holder);bridge._ensure_media();assert len(holder)==count
    finally:
        bridge._media_mapping=None
        for value in holder:
            if not value.closed:value.close()
        with patch.object(bridge,'release',return_value=None):bridge.close()

def p3():
    from dolly import editor_session as m
    from dolly.gui import DollyApp
    from dolly.path import Project,Keyframe,AttachKey
    from dolly.native_effects import model_token
    project=Project(keyframes=[Keyframe(0,0,0,0,0,0,0,source='attach',attach=AttachKey(handle=1,entity_id=1,model='hero'))])
    class Bridge:
        _editor_attach_sequence=20
        def __init__(self,offset):self.offset=offset;self.sequence=2
        def editor_status(self):return dict(enabled=True,events=[])
        def editor_attach_result(self):return dict(valid=True,sequence=self.sequence,config_sequence=20,
            handle=1,entity_index=1,model=model_token('hero'),point=0,offset=(self.offset,0,0,0,0,0))
    one,two=Bridge(1),Bridge(2);current=[one];seen=[]
    app=NS(closed=False,busy=False,native_editor_active=True,_attach_roster_at=float('inf'),
        _bone_picker_context=None,_attach_snap_pending=False,preview_attach=True,
        project=project,camera_tree=object(),_selection_index=lambda _:0,status_text=Mock(),
        controller=NS(status=lambda:dict(connected=True),_native_bridge=lambda:current[0]),
        _mark_dirty=Mock(),_refresh_attach_card=Mock())
    def handler(result):
        seen.append((current[0],result['sequence']))
        return DollyApp._attach_result_changed(app,result)
    app._attach_result_changed=handler
    with patch.object(m,'configure',return_value=None):
        m.poll(app);m.poll(app);assert len(seen)==1
        current[0]=two;m.poll(app)
        assert app.project.keyframes[0].attach.offset[0]==2,'new bridge result lost to old sequence'
        assert len(seen)==2
        m.poll(app);assert len(seen)==2
        two.sequence=4;two.offset=3;m.poll(app);assert len(seen)==3
        two.sequence=0;m.poll(app);assert len(seen)==3
        current[0]=None;m.poll(app)
        current[0]=one;m.poll(app);assert len(seen)==4

def p4():
    from dolly import settings as m
    from dolly.gui import DollyApp
    import dolly.gui as gui
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'settings.json';m.save_settings(m.AppSettings(),path)
        valid=json.loads(path.read_text(encoding='utf-8'))
        for name in ('movement_speed','mouse_sensitivity'):
            for number in (10**400,-10**400,float('inf'),float('nan')):
                data=copy.deepcopy(valid);data[name]=number
                raw=json.dumps(data).encode();path.write_bytes(raw)
                raises(ValueError,lambda:m.load_settings(path));assert path.read_bytes()==raw
        data=copy.deepcopy(valid);data['movement_speed']=10**400
        raw=json.dumps(data).encode();path.write_bytes(raw)
        app=NS(root=NS(after=Mock()),_enqueue_log=Mock())
        with patch.object(gui,'load_settings',side_effect=lambda:m.load_settings(path)):
            fallback=DollyApp._load_app_settings(app)
        assert fallback==m.AppSettings() and app.root.after.call_count==1
        assert path.read_bytes()==raw
        m.save_settings(fallback,path)
        assert m.load_settings(path)==fallback
        backups=list(path.parent.glob('*.invalid'));assert len(backups)==1 and backups[0].read_bytes()==raw
        for name,bounds in [('movement_speed',(1,10000)),('mouse_sensitivity',(.001,10))]:
            for number in bounds:assert getattr(m.AppSettings(**{name:number}),name)==float(number)

def p6():
    from dolly import screenshot as m
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);video=root/'color.mkv';video.write_bytes(b'video')
        (root/'shot.json').write_bytes(b'sidecar');(root/'plate.png').write_bytes(b'final plate')
        (root/'depth'/'exr').mkdir(parents=True);(root/'players').mkdir()
        known=['depth/depth.mov','depth/manifest.json','depth/camera.json','depth/preview_2x2.raw',
               'depth/preview.mp4','depth/exr/00000008.exr','players/players.mov']
        unknown=['depth/notes.txt','depth/exr/keep.exr','players/my_grade.cube','players/nested/user.dat']
        for name in known+unknown:
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(name.encode())
        m.cleanup(root,video)
        assert all((root/name).is_file() for name in unknown),'still cleanup deleted unexpected files'
        assert all((root/name).read_bytes()==name.encode() for name in unknown)
        assert not any((root/name).exists() for name in known)
        assert (root/'plate.png').read_bytes()==b'final plate'
        assert not video.exists() and not (root/'shot.json').exists()
        m.cleanup(root,video);assert all((root/name).exists() for name in unknown)
        # Ordinary completed folder still removes all generated intermediates.
        ordinary=root/'ordinary';(ordinary/'depth'/'exr').mkdir(parents=True);(ordinary/'players').mkdir()
        (ordinary/'depth'/'exr'/'00000000.exr').write_bytes(b'frame')
        (ordinary/'depth'/'preview.mkv').write_bytes(b'preview')
        m.cleanup(ordinary,ordinary/'video.mkv')
        assert not (ordinary/'depth').exists() and not (ordinary/'players').exists()
        # A known basename that is a directory is unexpected, not a recursive target.
        (ordinary/'depth'/'depth.mov').mkdir(parents=True)
        (ordinary/'depth'/'depth.mov'/'note').write_bytes(b'user')
        m.cleanup(ordinary,ordinary/'video.mkv')
        assert (ordinary/'depth'/'depth.mov'/'note').read_bytes()==b'user'

def p7():
    from dolly import player_layer as m,player_scratch
    for fault in ('update','manifest','owner','rmdir'):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);deployment=root/'deployment';deployment.mkdir()
            data,request=player_scratch.create(root,fps=60,frames=2)
            marker=f'color-sequence-v4 request {request} speed 1 scratch {str(data).encode().hex()}\n'
            (deployment/'dolly_owner_start.txt').write_text(marker,encoding='ascii')
            for name in (m.BUNDLE_NAME,m.META_NAME,'capture-status.txt'):(data/name).write_bytes(b'raw done')
            layer=root/'players';layer.mkdir();(layer/'players.mov').write_bytes(b'final')
            unlink,rmdir=Path.unlink,Path.rmdir
            def removing(path,*args,**kwargs):
                if path==data/(player_scratch.MANIFEST if fault=='manifest' else player_scratch.OWNER) and fault in ('manifest','owner'):
                    raise PermissionError('locked terminal file')
                return unlink(path,*args,**kwargs)
            def removing_dir(path,*args,**kwargs):
                if fault=='rmdir' and path==data:raise PermissionError('directory held')
                return rmdir(path,*args,**kwargs)
            with patch.object(Path,'unlink',removing),patch.object(Path,'rmdir',removing_dir),contextlib.ExitStack() as stack:
                if fault=='update':stack.enter_context(patch.object(player_scratch,'update',side_effect=PermissionError('manifest locked')))
                try:left=m.cleanup_capture(deployment,layer)
                except OSError as exc:raise AssertionError('completed-take cleanup escaped instead of leftovers') from exc
            assert left==[str(data)],left
            assert (layer/'players.mov').read_bytes()==b'final'
            assert not (data/m.BUNDLE_NAME).exists()
    # Unknown sibling files still retain marker/manifest and never recurse.
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);deployment=root/'deployment';deployment.mkdir();layer=root/'players';layer.mkdir()
        data,request=player_scratch.create(root,fps=60,frames=2)
        (deployment/'dolly_owner_start.txt').write_text(f'color-sequence-v4 request {request} speed 1 scratch {str(data).encode().hex()}\n',encoding='ascii')
        (data/'user').write_bytes(b'user');assert m.cleanup_capture(deployment,layer)==[]
        assert (data/'user').read_bytes()==b'user' and (data/player_scratch.OWNER).is_file()

def p8():
    import tkinter as tk
    from dolly.ui.tween import WheelScroller
    root=tk.Tk();root.withdraw();retained=[];commands=[]
    try:
        for i in range(3):
            widget=tk.Canvas(root)
            scroller=WheelScroller(widget,lambda n:n,animator=NS(stop=lambda _:None))
            scroller._time(time.perf_counter());command=scroller._mark;commands.append(command)
            widget.destroy();retained.append(weakref.ref(scroller));del scroller,widget
        gc.collect()
        live=[name for name in commands if root.tk.call('info','commands',name)]
        assert not live,'destroyed scrollers retain Tcl commands on toplevel'
        assert not any(w() is not None for w in retained),'destroyed scrollers retained by command callbacks'
        widget=tk.Canvas(root);scroller=WheelScroller(widget,lambda n:n,animator=NS(stop=lambda _:None))
        scroller._time(time.perf_counter());root.update_idletasks()
        assert scroller.cost_ms>=0 and scroller._mark_job is None
        widget.destroy()
    finally:root.destroy()

def p9():
    from dolly import mod_policy as m
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);mount=root/'mod';mount.mkdir();item=mount/'unreadable';item.mkdir()
        plain=m._plain_path
        def inaccessible(path):
            if path==item:raise PermissionError('cannot inspect entry')
            return plain(path)
        for mixed in (False,True):
            if mixed:(mount/'models').mkdir()
            with patch.object(m,'_plain_path',side_effect=inaccessible):
                verdict=m.classify_mount(mount)
                assert not verdict['allowed'],'uninspected mount entry was accepted'
                assert 'could not be checked' in verdict['reason']
                assert m.classify_mount(mount,allow_overrides=True)['allowed']
                carried,excluded=m.split_mounts([('Game','mod')],root)
                assert not carried and len(excluded)==1
        (item/'readme.txt').write_bytes(b'user')
        assert m.classify_mount(mount)['allowed']
        assert (item/'readme.txt').read_bytes()==b'user'

def p10():
    from dolly import native_effects as m
    from dolly.path import Project,Keyframe
    project=Project(keyframes=[Keyframe(i,0,0,0,0,0,0) for i in range(128)])
    real_float=float;calls=[0]
    def counting(value):calls[0]+=1;return real_float(value)
    with patch.object(m,'float',counting,create=True):assert m.compile_attach(project)==b''
    assert calls[0]<=len(project.keyframes)*3,'free shot spent quadratic work building discarded attach intervals'
    invalid=Project(keyframes=[Keyframe(0,0,0,0,0,0,0)]);invalid.keyframes[0].time=float('nan')
    raises(ValueError,lambda:m.compile_attach(invalid))

def p11():
    from dolly import screenshot as m,player_layer,screenshot_ui
    def frame(bpp=8,draws=1,first=1,last=1,pixels=None,width=2,height=1):
        if pixels is None:pixels=struct.pack('<8e',0,0,0,1,0,0,0,0)
        return struct.pack('<8Q',player_layer.BUNDLE_MAGIC,width,height,draws,first,last,5,bpp)+pixels
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);bundle=root/'bundle.bin'
        valid=frame();pixels=valid[64:]
        bundle.write_bytes(valid+valid);assert m.read_bundle_frame(bundle,1)==(2,1,pixels)
        empty=frame(draws=0,first=0,last=0,pixels=bytes(16))
        bundle.write_bytes(valid+empty);assert m.read_bundle_frame(bundle,1)==(2,1,bytes(16))
        bad_frames=[frame(bpp=16),frame(bpp=0),frame(draws=0,first=1,last=0,pixels=bytes(16)),
                    frame(draws=0,first=0,last=1,pixels=bytes(16)),frame(draws=0,first=0,last=0),
                    frame(width=3),valid[:40],valid[:-1]]
        for bad in bad_frames:
            bundle.write_bytes(valid+bad);raises(RuntimeError,lambda:m.read_bundle_frame(bundle,1))
        # The actual worker must refuse before touching output/cleanup.
        deployment=root/'deployment';deployment.mkdir();(deployment/player_layer.BUNDLE_NAME).write_bytes(valid+frame(bpp=16))
        (deployment/player_layer.STATUS_NAME).write_text('complete',encoding='ascii')
        folder=root/'take';folder.mkdir();(folder/'shot.json').write_text(json.dumps(dict(video_file='take.mkv',frames_written=2)),encoding='utf-8')
        app=NS(_still_run=m.StillRun(project=None,options=NS(path=root/'take.mkv'),depth=False),_base_capture=object(),
            controller=NS(deployment_directory=lambda:deployment))
        with patch.object(m,'write_hero_stills') as write,patch.object(player_layer,'cleanup_capture') as cleanup:
            raises(RuntimeError,lambda:screenshot_ui.finish_player_capture(app,'players'))
            assert not write.called and not cleanup.called
        assert (deployment/player_layer.BUNDLE_NAME).is_file()


class NextTenRegressions(unittest.TestCase):
    def test_bridge_close_continuation(self):
        p1()

    def test_provisional_media_mapping(self):
        p2()

    def test_attach_result_session(self):
        p3()

    def test_settings_numeric_boundary(self):
        p4()

    def test_still_cleanup_known_files(self):
        p6()

    def test_completed_scratch_cleanup_result(self):
        p7()

    def test_scroll_command_owner(self):
        p8()

    def test_mod_entry_inspection(self):
        p9()

    def test_free_shot_attach_fastpath(self):
        p10()

    def test_selected_player_frame(self):
        p11()

