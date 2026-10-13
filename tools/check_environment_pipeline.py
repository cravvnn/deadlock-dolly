"""Offline synthetic PNG/FFmpeg end-to-end proof; never opens the game."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dolly.environment_capture import capture_environment
from dolly.environment_assemble import assemble_capture
from dolly.screenshot import _png

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('output',type=Path)
parser.add_argument('--ffmpeg',type=Path,required=True)
args=parser.parse_args()
args.output.mkdir(parents=True,exist_ok=False)

class SyntheticBackend:
    def snapshot(self): return {'synthetic':True,'replay_sha256':'a'*64,'game_build':'synthetic'}
    def prepare(self,plan): self.plan=plan
    def restore(self,snapshot): assert snapshot['synthetic']
    def capture(self,face,request,directory):
        n=self.plan['face_size'];q=(np.arange(n)+.5)/n*2-1
        rays=np.asarray(face['forward'])+q[None,:,None]*np.asarray(face['right'])-q[:,None,None]*np.asarray(face['up'])
        rays/=np.linalg.norm(rays,axis=-1,keepdims=True)
        linear=(rays+1)/2
        encoded=np.where(linear<=.0031308,linear*12.92,1.055*linear**(1/2.4)-.055)
        pixels=np.rint(encoded*255).astype(np.uint8)
        file=directory/(face['name']+'.png')
        _png(file,n,n,8,2,(row.tobytes() for row in pixels))
        return dict(face,request_id=request,file=str(file),width=n,height=n,
                    horizontal_fov=90,vertical_fov=90,scene_time=1.0,
                    scene_empty=True,hud_hidden=True,exposure_signature='synthetic-constant',
                    color_encoding='display_srgb8',evidence='synthetic; no game capture',
                    replay_sha256='a'*64,game_build='synthetic')

capture_environment(SyntheticBackend(),args.output/'faces',[0,0,0],64,face_size=512,panorama_width=2048)
image=assemble_capture(args.output/'faces',args.output/'synthetic-panorama.png',args.ffmpeg)
decoded=subprocess.run([str(args.ffmpeg),'-v','error','-i',str(image),'-frames:v','1','-f','rawvideo',
                       '-pix_fmt','rgb24','pipe:1'],capture_output=True,check=True,timeout=60,
                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
pixels=np.frombuffer(decoded.stdout,np.uint8).reshape(1024,2048,3)/255
actual=np.where(pixels<=.04045,pixels/12.92,((pixels+.055)/1.055)**2.4)
lon=((np.arange(2048)+.5)/2048-.5)*2*np.pi
lat=(.5-(np.arange(1024)+.5)/1024)*np.pi
expected=(np.stack(np.broadcast_arrays(np.cos(lat[:,None])*np.cos(lon),-np.cos(lat[:,None])*np.sin(lon),np.sin(lat[:,None])),axis=-1)+1)/2
error=float(abs(expected-actual).max())
assert error<.009,error
report=dict(status='PASS',scope=__doc__,resolution=[2048,1024],max_linear_error=error,
            decoded_rgb_checks=int(actual.size),game_launched=False)
(args.output/'verification.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
