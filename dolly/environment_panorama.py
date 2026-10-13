"""Fixed-centre cubemap planning and bounded panorama reprojection.

Source coordinates: +X forward, +Y left, +Z up. Images have a top-left
origin. Panorama centre faces +X; moving right turns toward -Y. These are
projection requirements, not a claim that a game capture satisfied them.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class Face:
    name: str
    pitch: float
    yaw: float
    forward: tuple
    right: tuple
    up: tuple


FACES = (
    Face('front', 0, 0, (1, 0, 0), (0, -1, 0), (0, 0, 1)),
    Face('right', 0, -90, (0, -1, 0), (-1, 0, 0), (0, 0, 1)),
    Face('back', 0, 180, (-1, 0, 0), (0, 1, 0), (0, 0, 1)),
    Face('left', 0, 90, (0, 1, 0), (1, 0, 0), (0, 0, 1)),
    Face('up', -90, 0, (0, 0, 1), (0, -1, 0), (-1, 0, 0)),
    Face('down', 90, 0, (0, 0, -1), (0, -1, 0), (1, 0, 0)),
)


def capture_plan(position, tick, *, face_size=2048, panorama_width=8192, face_fov=90):
    if (len(position) != 3 or any(type(x) not in (float, int) or not math.isfinite(x)
                                  for x in position)):
        raise ValueError('Capture position must contain three finite coordinates')
    if type(tick) is not int or tick < 0:
        raise ValueError('Capture needs a nonnegative replay tick')
    if type(face_size) is not int or face_size not in (512, 1024, 2048, 4096):
        raise ValueError('Choose a 512, 1024, 2048 or 4096 pixel face')
    _dimensions(panorama_width)
    if type(face_fov) not in (int, float) or face_fov not in (90, 100):
        raise ValueError("Face projection must be 90 or 100 degrees")
    return dict(schema='dolly-environment-plan-v1' if face_fov == 90 else 'dolly-environment-plan-v2', position=list(position), tick=tick,
                face_size=face_size, panorama_width=panorama_width,
                panorama_height=panorama_width//2,
                horizontal_fov=face_fov, vertical_fov=face_fov, aspect_ratio=1,
                faces=[dict(asdict(f), forward=list(f.forward), right=list(f.right), up=list(f.up),
                            position=list(position), tick=tick, roll=0) for f in FACES],
                requirements=['Fixed optical centre', 'Measured square '+str(int(face_fov))+'-degree projection',
                              'Stable scene and exposure across all faces',
                              'No HUD, actors or combat effects'],
                status='planned_not_captured')


def _dimensions(width):
    if type(width) is not int or width < 8 or width > 16384 or width % 2:
        raise ValueError('Panorama width must be even and between 8 and 16384')


def panorama_rows(images, width, *, block_rows=16, face_fov=90):
    """Yield float32 RGB blocks, preserving input range (including HDR >1).

    Inputs must already share a color space/exposure. No tone mapping, gamma
    conversion, optical-flow stitching or upscaling claim is made. Bilinear
    sampling reaches into adjacent faces at cube edges instead of clamping
    for legacy 90-degree faces. 100-degree faces use a clean overlap blend.
    Working arrays are bounded by block_rows rather than panorama height.
    """
    import numpy as np
    _dimensions(width)
    if type(block_rows) is not int or not 1 <= block_rows <= 64:
        raise ValueError('Block height must be between 1 and 64')
    if set(images) != {f.name for f in FACES}:
        raise ValueError('Exactly six named cubemap faces are required')
    size = None
    for image in images.values():
        if (not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3
                or image.shape[0] != image.shape[1] or not 2 <= image.shape[0] <= 4096
                or image.dtype.kind not in 'uf'):
            raise ValueError('Faces must be square RGB numeric arrays, size 2..4096')
        if size is not None and size != image.shape[0]:
            raise ValueError('Face dimensions differ')
        size = image.shape[0]
        for y in range(0, size, 64):
            if not np.isfinite(image[y:y+64]).all():
                raise ValueError('Face contains nonfinite pixels')
    if type(face_fov) not in (int, float) or face_fov not in (90, 100):
        raise ValueError("Face projection must be 90 or 100 degrees")
    if face_fov == 100 and size < 128:
        raise ValueError("Overlap projection needs at least 128 pixels per face")
    height = width//2
    longitude = ((np.arange(width, dtype=np.float64)+.5)/width-.5)*2*math.pi
    for start in range(0, height, block_rows):
        latitude = (.5-(np.arange(start, min(start+block_rows, height))+.5)/height)*math.pi
        coslat = np.cos(latitude)[:, None]
        directions = np.stack(np.broadcast_arrays(coslat*np.cos(longitude),
            -coslat*np.sin(longitude), np.sin(latitude)[:, None]), axis=-1)
        if face_fov == 100:
            yield start, _overlap_sample(images, directions, size)
            continue
        # Largest positive face-normal dot product selects each ray's cube face.
        dots = np.stack([directions @ np.asarray(f.forward) for f in FACES], axis=-1)
        selected = dots.argmax(axis=-1)
        result = np.empty(directions.shape, dtype=np.float32)
        for index, face in enumerate(FACES):
            mask = selected == index
            rays = directions[mask]
            distance = dots[..., index][mask]
            u = ((rays @ np.asarray(face.right))/distance+1)*size*.5-.5
            v = (1-(rays @ np.asarray(face.up))/distance)*size*.5-.5
            x0, y0 = np.floor(u).astype(np.int32), np.floor(v).astype(np.int32)
            x1, y1 = x0+1, y0+1
            fx, fy = (u-x0)[:, None], (v-y0)[:, None]
            top = _cube_texels(images, face, x0, y0, size)*(1-fx) + _cube_texels(images, face, x1, y0, size)*fx
            bottom = _cube_texels(images, face, x0, y1, size)*(1-fx) + _cube_texels(images, face, x1, y1, size)*fx
            result[mask] = top*(1-fy) + bottom*fy
        yield start, result


def _cube_texels(images, face, x, y, size):
    """Fetch one bilinear tap, projecting out-of-face texels onto a neighbour.

    The outboard texel centre defines a cube direction; its nearest texel on
    the adjacent face supplies that tap. Working storage follows the row block.
    Lighting/color discontinuities in the input images remain visible.
    """
    import numpy as np
    inside = (x >= 0) & (x < size) & (y >= 0) & (y < size)
    values = np.empty((len(x), 3), dtype=np.float32)
    values[inside] = images[face.name][y[inside], x[inside]]
    outside = ~inside
    if outside.any():
        rays = (np.asarray(face.forward)
                + ((x[outside]+.5)*2/size-1)[:,None]*np.asarray(face.right)
                + (1-(y[outside]+.5)*2/size)[:,None]*np.asarray(face.up))
        dots = np.stack([rays @ np.asarray(f.forward) for f in FACES], axis=-1)
        selected = dots.argmax(axis=-1)
        adjacent = np.empty((len(rays),3),dtype=np.float32)
        for index, neighbour in enumerate(FACES):
            mask = selected == index
            r = rays[mask]
            distance = dots[mask,index]
            tx = ((r @ np.asarray(neighbour.right))/distance+1)*size*.5
            ty = (1-(r @ np.asarray(neighbour.up))/distance)*size*.5
            ix = np.clip(np.floor(tx).astype(np.int32),0,size-1)
            iy = np.clip(np.floor(ty).astype(np.int32),0,size-1)
            adjacent[mask] = images[neighbour.name][iy,ix]
        values[outside] = adjacent
    return values


def _overlap_sample(images, directions, size):
    """Pinhole reprojection with a normalized partition of unity in linear RGB.

    q=(dot(ray,right),-dot(ray,up))/dot(ray,forward); pixel=(q/t+1)*N/2-.5,
    t=tan(50deg). Four outer pixels and their bilinear footprints are excluded.
    Weight is one within the canonical face, smoothly fades to zero at the
    safe overlap boundary. Sum normalization preserves constant radiance,
    including cube corners. No exposure fitting, warping or image-space blur.
    """
    import numpy as np
    tangent = math.tan(math.radians(50))
    safe = tangent*(1-2*4.5/size)
    if safe <= 1:
        raise ValueError('Insufficient clean overlap')
    total = np.zeros(directions.shape, dtype=np.float64)
    weights = np.zeros(directions.shape[:-1], dtype=np.float64)
    for face in FACES:
        z = directions @ np.asarray(face.forward)
        positive = z > 0
        qx = np.zeros_like(z); qy = np.zeros_like(z)
        np.divide(directions @ np.asarray(face.right), z, out=qx, where=positive)
        np.divide(-(directions @ np.asarray(face.up)), z, out=qy, where=positive)
        radius = np.maximum(np.abs(qx), np.abs(qy))
        valid = positive & (radius < safe)
        u = (qx[valid]/tangent+1)*size/2-.5
        v = (qy[valid]/tangent+1)*size/2-.5
        x = np.floor(u).astype(np.int32); y = np.floor(v).astype(np.int32)
        fx = (u-x)[:,None]; fy = (v-y)[:,None]
        a = images[face.name]
        rgb = (a[y,x]*(1-fx)+a[y,x+1]*fx)*(1-fy)+(a[y+1,x]*(1-fx)+a[y+1,x+1]*fx)*fy
        t = np.clip((safe-radius[valid])/(safe-1),0,1)
        weight = t*t*(3-2*t)
        total[valid] += rgb*weight[:,None]
        weights[valid] += weight
    if not np.all(weights > 0):
        raise ValueError('Overlap projection left an uncovered direction')
    return (total/weights[...,None]).astype(np.float32)
