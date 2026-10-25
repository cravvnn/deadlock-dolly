"""Lossless float-RGB EXR output, independent of game acquisition.

Uses the same uncompressed scanline format as the native depth EXR writer.
No tone mapping, transfer function, exposure compensation or gamut conversion
is performed. An EXR container does not establish scene-linear radiance.
"""
from pathlib import Path
import os
import struct


def read_float_rgb_exr(path, width, height):
    """Read Dolly's uncompressed, ordered FLOAT RGB EXRs with bounded allocation.

    Deliberately rejects arbitrary EXR variants. Used for native face exports,
    not as a general-purpose image decoder or a claim about their color space.
    """
    import numpy as np
    if (type(width) is not int or type(height) is not int or
            not 1 <= width <= 16384 or not 1 <= height <= 16384 or
            width * height > 128 * 1024 * 1024):
        raise ValueError('Invalid float RGB image dimensions')
    with Path(path).open('rb') as stream:
        def exact(size):
            data=stream.read(size)
            if len(data)!=size:raise ValueError('Truncated EXR')
            return data
        def string():
            result=bytearray()
            for _ in range(256):
                c=exact(1)
                if c==b'\0':return bytes(result)
                result.extend(c)
            raise ValueError('Oversized EXR attribute name')
        if exact(8)!=struct.pack('<II',20000630,2):
            raise ValueError('Expected a single-part scanline EXR')
        fields={}
        while True:
            if stream.tell()>65536:raise ValueError('Oversized EXR header')
            name=string()
            if not name:break
            kind=string();size=struct.unpack('<I',exact(4))[0]
            if size>65536 or name in fields:raise ValueError('Invalid EXR attribute')
            fields[name]=(kind,exact(size))
        channels=b''.join(n+b'\0'+struct.pack('<iB3xii',2,0,1,1) for n in (b'B',b'G',b'R'))+b'\0'
        required={b'channels':(b'chlist',channels),b'compression':(b'compression',b'\0'),
                  b'dataWindow':(b'box2i',struct.pack('<4i',0,0,width-1,height-1)),
                  b'lineOrder':(b'lineOrder',b'\0')}
        if any(fields.get(k)!=v for k,v in required.items()):
            raise ValueError('Expected Dolly FLOAT RGB EXR at the declared dimensions')
        offsets=struct.unpack('<'+'Q'*height,exact(height*8))
        first=stream.tell();row_bytes=width*12
        if offsets!=tuple(first+y*(row_bytes+8) for y in range(height)):
            raise ValueError('Invalid EXR scanline offsets')
        if os.fstat(stream.fileno()).st_size!=first+height*(row_bytes+8):
            raise ValueError('EXR byte count does not match its dimensions')
        image=np.empty((height,width,3),dtype='<f4')
        for y in range(height):
            if exact(8)!=struct.pack('<ii',y,row_bytes):raise ValueError('Invalid EXR scanline')
            row=np.frombuffer(exact(row_bytes),dtype='<f4').reshape(3,width)
            if not np.isfinite(row).all():raise ValueError('Nonfinite EXR pixels')
            image[y]=row[::-1].T
        return image


def write_half_rgba_exr(output, width, height, pixels):
    """Export tightly packed native little-endian RGBA16F without exposure edits.

    RGB half values convert exactly to float32. Alpha is discarded. RGB NaN/Inf
    is rejected by the writer; neither this adapter nor EXR certifies radiometry.
    """
    import numpy as np
    if (type(width) is not int or type(height) is not int or
            not 1 <= width <= 4096 or not 1 <= height <= 4096):
        raise ValueError('Native HDR dimensions must be within 4096 x 4096')
    # Own immutable bytes at the API boundary: caller mutation must not change
    # rows during output. bytes inputs incur no extra full-frame allocation.
    if not isinstance(pixels, bytes):
        raise ValueError('Native HDR pixels must be immutable bytes')
    if len(pixels) != width * height * 8:
        raise ValueError('Native HDR byte count does not match RGBA16F dimensions')
    array = np.frombuffer(pixels, dtype='<f2').reshape(height, width, 4)
    return write_float_rgb_exr(output, width, height, (row[:, :3] for row in array))


def write_float_rgb_exr(output, width, height, rows):
    """Write exactly height top-to-bottom RGB float rows without clipping.

    Rows must have shape (width, 3). Finite negative and above-one values are
    preserved as FLOAT32. Publication refuses an existing output; a failed row
    generator never leaves a completed-looking output. Working memory is one row.
    """
    import numpy as np
    if (type(width) is not int or type(height) is not int or
            not 1 <= width <= 16384 or not 1 <= height <= 16384 or
            width * height > 128 * 1024 * 1024):
        raise ValueError('Invalid float RGB image dimensions')
    output = Path(output)
    if output.suffix.lower() != '.exr':
        raise ValueError('Float RGB output requires an .exr filename')
    partial = output.with_name(output.name + '.partial')
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    header = bytearray(struct.pack('<II', 20000630, 2))
    def attribute(name, kind, value):
        header.extend(name.encode('ascii') + b'\0' + kind.encode('ascii') + b'\0')
        header.extend(struct.pack('<I', len(value)) + value)
    # EXR stores each scanline in alphabetically ordered channel planes.
    channels = b''.join(name + b'\0' + struct.pack('<iB3xii', 2, 0, 1, 1)
                        for name in (b'B', b'G', b'R')) + b'\0'
    attribute('channels', 'chlist', channels)
    attribute('compression', 'compression', b'\0')
    window = struct.pack('<4i', 0, 0, width-1, height-1)
    attribute('dataWindow', 'box2i', window)
    attribute('displayWindow', 'box2i', window)
    attribute('lineOrder', 'lineOrder', b'\0')
    attribute('pixelAspectRatio', 'float', struct.pack('<f', 1))
    attribute('screenWindowCenter', 'v2f', struct.pack('<2f', 0, 0))
    attribute('screenWindowWidth', 'float', struct.pack('<f', 1))
    attribute('dollyDataMeaning', 'string',
              b'Float RGB; scene-linear radiance and color space require separate acquisition proof')
    header.append(0)
    row_bytes = width * 12
    first = len(header) + height * 8
    iterator = iter(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive temporary creation preserves any earlier incomplete evidence.
    with partial.open('xb') as stream:
        try:
            stream.write(header)
            for y in range(height):
                stream.write(struct.pack('<Q', first + y * (row_bytes + 8)))
            for y in range(height):
                try:
                    raw = next(iterator)
                except StopIteration as error:
                    raise ValueError('Too few float RGB rows') from error
                with np.errstate(over='ignore', invalid='ignore'):
                    row = np.asarray(raw, dtype='<f4')
                if row.shape != (width, 3) or not np.isfinite(row).all():
                    raise ValueError('Expected finite float32 RGB row at the declared width')
                stream.write(struct.pack('<ii', y, row_bytes))
                for channel in (2, 1, 0):
                    stream.write(row[:, channel].tobytes())
            sentinel = object()
            if next(iterator, sentinel) is not sentinel:
                raise ValueError('Too many float RGB rows')
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            # Keep failed partial data for diagnosis; never publish as .exr.
            raise
    # Atomic, non-replacing publication on the same volume. If the final name
    # appeared meanwhile, leave its bytes untouched and retain our partial.
    os.link(partial, output)
    partial.unlink()
    return output
