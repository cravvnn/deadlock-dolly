"""Bounded native HDR envelope reader/exporter; live IPC is supplied separately.

The producer sends one 88-byte header then tightly packed RGBA16F. Request token
and reviewed shader digest must be provided by the owning capture transaction.
This does not certify camera position, empty scenery or scene-linear radiance.
"""
import math
import struct

from .environment_hdr import write_half_rgba_exr

HEADER = struct.Struct('<8sII16sIIQ2f32s')


def export_frame(stream, output, *, request, shader_sha256, cancelled=lambda: False):
    """Read a finite frame stream and export only a complete matching response.

Transport must impose its own read deadline and close on cancellation. Reads are
bounded to 1 MiB and checked for cancellation between calls. No pipe is opened
here; caller supplies a stream ending immediately after this single packet.
"""
    if (type(request) is not bytes or len(request) != 16 or not any(request) or
            type(shader_sha256) is not bytes or len(shader_sha256) != 32):
        raise ValueError('Expected capture token and reviewed shader digest')

    def exact(count):
        # One allocation for the payload, no list of full-frame copies.
        data = bytearray(count)
        offset = 0
        while offset < count:
            if cancelled():
                raise InterruptedError('HDR transfer cancelled')
            chunk = stream.read(min(count-offset, 1024*1024))
            if not isinstance(chunk, bytes) or not chunk or len(chunk) > count-offset:
                raise ValueError('Incomplete or invalid HDR transfer')
            data[offset:offset+len(chunk)] = chunk
            offset += len(chunk)
        return bytes(data)

    magic, abi, size, token, width, height, count, view, post, digest = HEADER.unpack(exact(HEADER.size))
    if magic != b'DLYHDR01' or abi != 1 or size != HEADER.size:
        raise ValueError('Unsupported HDR transfer header')
    if token != request or digest != shader_sha256:
        raise ValueError('Stale capture or unreviewed HDR shader')
    if not 1 <= width <= 4096 or not 1 <= height <= 4096 or count != width*height*8:
        raise ValueError('Invalid HDR transfer dimensions or byte count')
    if any(not math.isfinite(v) or v <= 0 for v in (view, post)):
        raise ValueError('Invalid HDR exposure snapshot')
    pixels = exact(count)
    if cancelled():
        raise InterruptedError('HDR transfer cancelled')
    if stream.read(1) != b'':
        raise ValueError('Trailing HDR transfer data')
    if cancelled():
        raise InterruptedError('HDR transfer cancelled')
    write_half_rgba_exr(output, width, height, pixels)
    return dict(width=width, height=height, request_id=request.hex(),
                shader_sha256=digest.hex(), exposure_view=view, exposure_post=post,
                color_encoding='unqualified_pre_tonemap_rgba16f')
