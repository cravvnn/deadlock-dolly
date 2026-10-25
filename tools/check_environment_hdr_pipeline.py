"""Synthetic native-half -> six EXRs -> panorama -> independent FFmpeg proof.

Never opens the game. Direction colors test seams, axes, range and row order;
synthetic capture receipts are explicitly labelled and are not live evidence.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dolly.environment_capture import capture_environment
from dolly.environment_assemble import assemble_hdr_capture
from dolly.environment_hdr import write_half_rgba_exr
from dolly.runtime import external_program_environment


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--ffmpeg',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)

    class SyntheticBackend:
        def snapshot(self):return dict(synthetic=True,replay_sha256='a'*64,game_build='synthetic')
        def prepare(self,plan):self.plan=plan
        def restore(self,snapshot):assert snapshot['synthetic']
        def capture(self,face,request,directory):
            n=self.plan['face_size'];q=(np.arange(n)+.5)/n*2-1
            rays=(np.asarray(face['forward'])+q[None,:,None]*np.asarray(face['right'])
                  -q[:,None,None]*np.asarray(face['up']))
            rays/=np.linalg.norm(rays,axis=-1,keepdims=True)
            pixels=np.ones((n,n,4),dtype='<f2');pixels[:,:,:3]=(rays+1)*4
            file=directory/(face['name']+'.exr')
            write_half_rgba_exr(file,n,n,pixels.tobytes())
            return dict(face,request_id=request,file=str(file),width=n,height=n,
                horizontal_fov=90,vertical_fov=90,scene_time=1.0,scene_empty=True,hud_hidden=True,
                exposure_signature='synthetic-fixed',color_encoding='scene_linear_float',
                evidence='SYNTHETIC; no game capture',replay_sha256='a'*64,game_build='synthetic')

    capture_environment(SyntheticBackend(),args.output/'faces',[0,0,0],64,
                        face_size=512,panorama_width=1024)
    panorama=assemble_hdr_capture(args.output/'faces',args.output/'synthetic-hdr-panorama.exr')
    with external_program_environment() as env:
        result=subprocess.run([str(args.ffmpeg),'-v','error','-nostdin','-i',str(panorama),
            '-frames:v','1','-f','rawvideo','-pix_fmt','gbrpf32le','pipe:1'],
            capture_output=True,check=True,timeout=60,env=env,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    actual=np.frombuffer(result.stdout,dtype='<f4').reshape(3,512,1024)[[2,0,1]].transpose(1,2,0)
    lon=((np.arange(1024)+.5)/1024-.5)*2*np.pi
    lat=(.5-(np.arange(512)+.5)/512)*np.pi
    expected=(np.stack(np.broadcast_arrays(np.cos(lat[:,None])*np.cos(lon),
        -np.cos(lat[:,None])*np.sin(lon),np.sin(lat[:,None])),axis=-1)+1)*4
    error=float(np.abs(actual-expected).max())
    assert np.isfinite(actual).all() and error<.01 and actual.max()>7.9,error
    report=dict(status='PASS',game_launched=False,evidence='synthetic only',
        resolution=[1024,512],max_error=error,max_value=float(actual.max()),
        checked_components=int(actual.size),path=str(panorama))
    (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
