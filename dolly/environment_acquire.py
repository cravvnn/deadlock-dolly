"""One bounded diagnostic HDR readback from an already-owned paused replay.

No camera, exposure, scene filtering or launch changes. Not a qualified six-face
backend: its caller must still establish projection, scene and radiometry.
"""
import mmap
import struct
import time
import uuid

from . import edition
from .environment_frame import export_frame
from .native_errors import NativeBridgeError

REQUEST_OFFSET = 2*1024*1024+25856
STATUS_OFFSET = 2*1024*1024+25984
REQUEST = struct.Struct('<8s12I16s')
STATUS = struct.Struct('<8s5I')
# Exact static0/dynamic6 variant independently observed in the live replay.
REVIEWED_SHADER = bytes.fromhex('ae9e7c352e80103c421f1bd43f75897f7c131a35cc692a32acf7d5037ba53e7b')


class Acquisition:
    def __init__(self, bridge):
        self.bridge = bridge
        self.command = 0

    def send(self, enabled, token, camera, tick, width, height):
        for value, maximum in ((camera, 0xffffffff), (tick, 0xffffffff), (width,4096),(height,4096)):
            if type(value) is not int or value < 0 or value > maximum:
                raise ValueError('Invalid HDR request bounds')
        if type(enabled) is not bool or type(token) is not bytes or len(token)!=16:
            raise ValueError('Invalid HDR request identity')
        if enabled and (not camera or not width or not height or not any(token)):
            raise ValueError('Incomplete HDR request')
        bridge=self.bridge
        with bridge._lock:
            bridge._check_open()
            if not bridge.game_pid: raise NativeBridgeError('HDR capture requires an owned game')
            previous=REQUEST.unpack(bytes(bridge._mapping[REQUEST_OFFSET:REQUEST_OFFSET+REQUEST.size]))
            command=previous[3]+1 if previous[0]==b'DLYHDRR1' else 1
            if command>0xffffffff:raise NativeBridgeError('HDR command counter exhausted')
            odd=((previous[1]|1)+2)&0xffffffff
            data=REQUEST.pack(b'DLYHDRR1',odd,1,command,int(enabled),bridge.editor_pid,bridge.game_pid,
                              camera,tick,width,height,5000 if enabled else 0,0,token)
            bridge._store(REQUEST_OFFSET+8,odd)
            bridge._mapping[REQUEST_OFFSET:REQUEST_OFFSET+8]=data[:8]
            bridge._mapping[REQUEST_OFFSET+12:REQUEST_OFFSET+REQUEST.size]=data[12:]
            bridge._store(REQUEST_OFFSET+8,(odd+1)&0xffffffff)
            self.command=command
        return command

    def status(self):
        bridge=self.bridge
        with bridge._lock:
            bridge._check_open()
            for _ in range(8):
                before=bridge._load_sequence(STATUS_OFFSET+8)
                if before&1:continue
                data=bytes(bridge._mapping[STATUS_OFFSET:STATUS_OFFSET+STATUS.size])
                after=bridge._load_sequence(STATUS_OFFSET+8)
                magic,sequence,abi,command,state,size=STATUS.unpack(data)
                if before!=after or sequence!=before or after&1:continue
                if magic!=b'DLYHDRS1' or abi!=1:return None
                if state>7:raise NativeBridgeError('Invalid HDR acquisition status')
                return dict(command=command,state=state,bytes=size)
        raise NativeBridgeError('HDR status remained busy')


def capture_hdr_frame(controller, output, *, width, height, cancelled=lambda:False,
                      mapping_factory=None, clock=time.monotonic, sleep=time.sleep):
    if not edition.is_environment():
        raise RuntimeError('HDR acquisition is available only in the environment edition')
    if any(type(v) is not int or not 1<=v<=4096 for v in (width,height)):
        raise ValueError('HDR dimensions must be within 4096 x 4096')
    controller._require_connection()
    bridge=controller._native_bridge()
    if bridge is None:raise RuntimeError('HDR acquisition requires the native backend')
    with bridge._lock:
        if getattr(bridge,'_environment_capture_active',False):
            raise RuntimeError('An HDR acquisition is already running')
        bridge._environment_capture_active=True
    acquisition=Acquisition(bridge)
    mapping=None; armed=False; token=uuid.uuid4().bytes
    try:
        initial=bridge.status()
        controller._require_native_demo(initial,allow_idle=True)
        camera,tick=initial['ack_command'],initial['tick']
        def verify():
            if cancelled():raise InterruptedError('HDR capture cancelled')
            controller._require_connection()
            if controller._native_bridge() is not bridge:raise RuntimeError('HDR game session changed')
            now=bridge.status()
            controller._require_native_demo(now,allow_idle=True)
            if (not now.get('paused') or now.get('error') or now.get('tick')!=tick or
                    now.get('ack_command')!=camera or now.get('demo_name')!=initial.get('demo_name')):
                raise RuntimeError('HDR replay or camera changed during acquisition')
        verify()
        name=f'Local\\DollyEnvironmentFrame-{bridge.editor_pid}-{bridge.game_pid}-{token.hex()}'
        size=88+width*height*8
        mapping=(mapping_factory(name,size) if mapping_factory else
                 mmap.mmap(-1,size,tagname=name,access=mmap.ACCESS_WRITE))
        if mapping[:88]!=bytes(88):raise RuntimeError('HDR transfer mapping was already populated')
        acquisition.send(True,token,camera,tick,width,height);armed=True
        deadline=clock()+7
        while True:
            verify()
            status=acquisition.status()
            if status and status['command']==acquisition.command:
                if status['state']==4:
                    if status['bytes']!=size:raise RuntimeError('Incomplete HDR frame delivery')
                    break
                if status['state']>=5:raise RuntimeError('HDR acquisition failed (state '+str(status['state'])+')')
            elif status and status['command']>acquisition.command:
                raise RuntimeError('HDR acquisition command was replaced')
            if clock()>=deadline:raise TimeoutError('HDR acquisition exceeded seven seconds')
            sleep(.02)
        verify();mapping.seek(0)
        result=export_frame(mapping,output,request=token,shader_sha256=REVIEWED_SHADER,cancelled=cancelled)
        result.update(tick=tick,camera_command=camera,demo_name=initial.get('demo_name'),qualified_environment=False)
        return result
    finally:
        try:
            if armed and not bridge._closed:
                status=acquisition.status()
                if status is None or status['command']<=acquisition.command:
                    acquisition.send(False,token,0,0,0,0)
        finally:
            if mapping is not None:mapping.close()
            with bridge._lock:bridge._environment_capture_active=False
