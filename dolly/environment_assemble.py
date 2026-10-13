"""Assemble measured cubemap captures into a background PNG or floating EXR.

This path deliberately does not label screenshot RGB as HDR illumination.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

from .environment_panorama import FACES, panorama_rows, capture_plan
from .environment_capture import validate_face
from .runtime import external_program_environment
from .screenshot import _png


def assemble_capture(folder, output, ffmpeg, *, width=None):
    import numpy as np
    folder, output = Path(folder).resolve(), Path(output).resolve()
    if output.exists() or output.with_name(output.name+'.partial').exists():
        raise ValueError('Choose a new output filename')
    if output.suffix.lower() != '.png':
        raise ValueError('Screenshot panorama output must be PNG; this is not HDR capture')
    report = json.loads((folder/'capture.json').read_text())
    if report.get('schema') != 'dolly-environment-capture-v1' or report.get('status') != 'captured' or report.get('restored') is not True:
        raise ValueError('Capture is incomplete or restoration was not verified')
    plan = report['plan']
    canonical = capture_plan(plan['position'], plan['tick'],face_size=plan['face_size'],panorama_width=plan['panorama_width'], face_fov=plan['horizontal_fov'])
    if plan != canonical: raise ValueError('Capture projection plan differs from the supported convention')
    rows = report['faces']
    if len(rows) != 6 or {r['face'] for r in rows} != {f.name for f in FACES}:
        raise ValueError('Exactly six unique captured faces required')
    images = {}
    reference = None
    for face in plan['faces']:
        row = next(r for r in rows if r['face'] == face['name'])
        validate_face(plan, face, row, row['request_id'])
        if row['color_encoding'] != 'display_srgb8':
            raise ValueError('This assembler only handles display sRGB backgrounds')
        signature = (row['exposure_signature'], row['replay_sha256'], row['game_build'], row['scene_time'])
        if reference is not None and (signature[:3] != reference[:3] or abs(signature[3]-reference[3]) > 1e-6):
            raise ValueError('Inconsistent face exposure/time')
        reference = signature
        path = folder/row['file']
        if path.is_symlink() or path.resolve().parent != folder:
            raise ValueError('Face path is outside the capture directory')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            stream.seek(0); header = stream.read(33)
        if digest != row['sha256']: raise ValueError('Captured face changed: '+face['name'])
        size = plan['face_size']
        if (len(header) != 33 or header[:8] != b'\x89PNG\r\n\x1a\n' or header[12:16] != b'IHDR'
                or struct.unpack('>II',header[16:24]) != (size,size) or header[24:26] != bytes([8,2])):
            raise ValueError('Expected a captured 8-bit RGB PNG at the measured dimensions')
        with external_program_environment() as environment:
            process = subprocess.run([str(ffmpeg), '-v','error','-nostdin','-i',str(path),
                '-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],
                capture_output=True, timeout=120, env=environment,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if process.returncode or len(process.stdout) != size*size*3:
            raise ValueError('Could not decode face '+face['name']+': '+process.stderr.decode(errors='replace')[-1000:])
        images[face['name']] = np.frombuffer(process.stdout, np.uint8).reshape(size,size,3)
    width = plan['panorama_width'] if width is None else width
    # Transfer to linear light before interpolation; encode display sRGB afterward.
    lookup = np.arange(256,dtype=np.float32)/255
    lookup = np.where(lookup <= .04045, lookup/12.92, ((lookup+.055)/1.055)**2.4)
    linear = {name:lookup[pixels] for name,pixels in images.items()}
    del images
    def encoded_rows():
        for _, block in panorama_rows(linear, width, face_fov=plan['horizontal_fov']):
            encoded = np.where(block <= .0031308, block*12.92, 1.055*np.maximum(block,0)**(1/2.4)-.055)
            pixels = np.rint(np.clip(encoded,0,1)*255).astype(np.uint8)
            yield from (row.tobytes() for row in pixels)
    output.parent.mkdir(parents=True,exist_ok=True)
    _png(output,width,width//2,8,2,encoded_rows())
    return output


def assemble_hdr_capture(folder, output, *, width=None, rendered_reference=False, cancelled=lambda:False):
    """Assemble qualified native FLOAT RGB faces without tone mapping or FFmpeg.

    All six capture receipts must already establish scene-linear data, empty
    scenery, equal exposure/time and camera identity. EXR alone is insufficient.
    """
    from .environment_hdr import read_float_rgb_exr, write_float_rgb_exr
    from .environment_capture import CaptureCancelled
    def check():
        if cancelled():raise CaptureCancelled('Panorama assembly cancelled; captured faces retained')
    check()
    folder,output=Path(folder).resolve(),Path(output).resolve()
    if output.suffix.lower()!='.exr':raise ValueError('HDR panorama output must be EXR')
    if output.exists() or output.with_name(output.name+'.partial').exists():
        raise ValueError('Choose a new output filename')
    report=json.loads((folder/'capture.json').read_text())
    schema='dolly-environment-reference-v1' if rendered_reference else 'dolly-environment-capture-v1'
    if (report.get('schema')!=schema or
            report.get('status')!='captured' or report.get('restored') is not True):
        raise ValueError('Capture is incomplete or restoration was not verified')
    plan=report['plan']
    canonical=capture_plan(plan['position'],plan['tick'],face_size=plan['face_size'],
                           panorama_width=plan['panorama_width'], face_fov=plan['horizontal_fov'])
    if plan!=canonical:raise ValueError('Capture projection plan differs from the supported convention')
    if width is not None:
        canonical=capture_plan(plan['position'],plan['tick'],face_size=plan['face_size'],panorama_width=width, face_fov=plan['horizontal_fov'])
    rows=report['faces']
    if len(rows)!=6 or {r['face'] for r in rows}!={f.name for f in FACES}:
        raise ValueError('Exactly six unique captured faces required')
    images={};reference=None
    for face in plan['faces']:
        check()
        row=next(r for r in rows if r['face']==face['name'])
        validate_face(plan,face,row,row['request_id'],rendered_reference=rendered_reference)
        expected='rendered_hdr_float' if rendered_reference else 'scene_linear_float'
        if row['color_encoding']!=expected:
            raise ValueError('HDR assembly requires qualified scene-linear faces')
        signature=(row['exposure_signature'],row['replay_sha256'],row['game_build'],row['scene_time'])
        if reference is not None and (signature[:3]!=reference[:3] or abs(signature[3]-reference[3])>1e-6):
            raise ValueError('Inconsistent face exposure/time')
        reference=signature
        path=folder/row['file']
        if path.is_symlink() or path.resolve().parent!=folder:raise ValueError('Face path outside capture directory')
        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=row['sha256']:raise ValueError('Captured face changed: '+face['name'])
        images[face['name']]=read_float_rgb_exr(path,plan['face_size'],plan['face_size'])
    def float_rows():
        for _,block in panorama_rows(images,canonical['panorama_width'], face_fov=plan['horizontal_fov']):
            check()
            yield from block
    return write_float_rgb_exr(output,canonical['panorama_width'],canonical['panorama_height'],float_rows())


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--ffmpeg',type=Path)
    parser.add_argument('--width',type=int)
    parser.add_argument('--hdr',action='store_true',help='Assemble validated float faces to EXR')
    args=parser.parse_args(argv)
    if args.hdr:
        print(assemble_hdr_capture(args.capture,args.output,width=args.width))
    else:
        if args.ffmpeg is None:parser.error('--ffmpeg is required for PNG assembly')
        print(assemble_capture(args.capture,args.output,args.ffmpeg,width=args.width))


if __name__ == '__main__': main()
