"""Synthetic WARP -> native envelope -> Python EXR interoperability check.

Runs only the test executable, never Deadlock. Temporary artifacts auto-clean.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dolly.environment_frame import export_frame
from dolly.environment_hdr import read_float_rgb_exr


def main():
    executable = ROOT/'build/native-msvc/Release/environment_source_tests.exe'
    with tempfile.TemporaryDirectory(prefix='dolly-hdr-wire-') as folder:
        packet = Path(folder)/'synthetic.bin'
        subprocess.run([str(executable), str(packet)], check=True, timeout=30,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        with packet.open('rb') as stream:
            header = stream.read(88)
            # This test's shader is compiled synthetically by WARP; its digest
            # isn't a reviewed game contract. Production must supply that review.
            digest = header[56:88]
            stream.seek(0)
            output = Path(folder)/'synthetic.exr'
            result = export_frame(stream, output, request=bytes([7])+bytes(15), shader_sha256=digest)
        image = read_float_rgb_exr(output, 63, 32)
        red = np.array([0x4873], dtype='<u2').view('<f2')[0]
        expected = np.broadcast_to(np.array([red, -1, .5], dtype='<f4'), image.shape)
        np.testing.assert_array_equal(image, expected)
        assert result['exposure_view']==2.5 and result['exposure_post']==4.0, result
        print('PASS: synthetic WARP GPU readback -> native packet -> Python float EXR; 63x32, HDR/sign/exposure exact')


if __name__ == '__main__':
    main()
