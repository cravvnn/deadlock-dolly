"""Run Windows build checks on a private, never-activated desktop.

Build children inherit the private desktop. Standard output/error still reach
the caller's build log. This never switches the user's desktop, launches a
game, disables tests or changes the application's normal desktop startup.
"""
from contextlib import ExitStack
import ctypes
from ctypes import wintypes as w
import os
import subprocess
import sys
import uuid

_MARKER = 'DOLLY_BUILD_TEST_DESKTOP'


def desktop_name():
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentThreadId.restype = w.DWORD
    user.GetThreadDesktop.argtypes = [w.DWORD]
    user.GetThreadDesktop.restype = w.HANDLE
    user.GetUserObjectInformationW.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    user.GetUserObjectInformationW.restype = w.BOOL
    name, needed = ctypes.create_unicode_buffer(256), w.DWORD()
    handle = user.GetThreadDesktop(kernel.GetCurrentThreadId())
    if not user.GetUserObjectInformationW(handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    return name.value


def isolate_build():
    """Return child exit code in the parent, None in the verified worker.

    Call only at a build CLI's entry point, after parsing its arguments. An
    unavailable private desktop fails the build rather than flashing test UI.
    """
    if sys.platform != 'win32':
        return None
    actual = desktop_name()
    expected = os.environ.get(_MARKER)
    if expected is not None:
        if not expected.startswith('DollyBuild_') or actual != expected:
            raise RuntimeError('Build desktop could not be verified; refusing visible UI checks.')
        return None
    import msvcrt
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user.CreateDesktopW.argtypes = [w.LPCWSTR, w.LPCWSTR, ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.c_void_p]
    user.CreateDesktopW.restype = w.HANDLE
    user.CloseDesktop.argtypes = [w.HANDLE]
    user.CloseDesktop.restype = w.BOOL
    class Startup(ctypes.Structure):
        _fields_ = [('cb', w.DWORD), ('lpReserved', w.LPWSTR), ('lpDesktop', w.LPWSTR), ('lpTitle', w.LPWSTR),
                    ('dwX', w.DWORD), ('dwY', w.DWORD), ('dwXSize', w.DWORD), ('dwYSize', w.DWORD),
                    ('dwXCountChars', w.DWORD), ('dwYCountChars', w.DWORD), ('dwFillAttribute', w.DWORD),
                    ('dwFlags', w.DWORD), ('wShowWindow', w.WORD), ('cbReserved2', w.WORD),
                    ('lpReserved2', ctypes.c_void_p), ('hStdInput', w.HANDLE), ('hStdOutput', w.HANDLE), ('hStdError', w.HANDLE)]
    class Extended(ctypes.Structure):
        _fields_ = [('startup', Startup), ('attributes', ctypes.c_void_p)]
    class Process(ctypes.Structure):
        _fields_ = [('process', w.HANDLE), ('thread', w.HANDLE), ('pid', w.DWORD), ('tid', w.DWORD)]
    kernel.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    kernel.InitializeProcThreadAttributeList.restype = w.BOOL
    kernel.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
    kernel.UpdateProcThreadAttribute.restype = w.BOOL
    kernel.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD,
                                     ctypes.c_void_p, w.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(Process)]
    kernel.CreateProcessW.restype = w.BOOL
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.WaitForSingleObject.restype = w.DWORD
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    kernel.GetExitCodeProcess.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    name = 'DollyBuild_' + uuid.uuid4().hex
    desktop = user.CreateDesktopW(name, None, None, 0, 0x10000000, None)
    if not desktop:
        raise ctypes.WinError(ctypes.get_last_error())
    attributes = None
    process = Process()
    try:
        size = ctypes.c_size_t()
        kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        if not 0 < size.value <= 65536:
            raise RuntimeError('Invalid Windows build attribute-list size.')
        storage = ctypes.create_string_buffer(size.value)
        if not kernel.InitializeProcThreadAttributeList(storage, 1, 0, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = storage
        with ExitStack() as stack:
            stdin = stack.enter_context(open(os.devnull, 'rb'))
            output = sys.stdout or stack.enter_context(open(os.devnull, 'wb'))
            errors = sys.stderr or output
            handles = [msvcrt.get_osfhandle(stream.fileno()) for stream in (stdin, output, errors)]
            unique = list(dict.fromkeys(handles))
            values = (w.HANDLE * len(unique))(*unique)
            if not kernel.UpdateProcThreadAttribute(attributes, 0, 0x20002, values, ctypes.sizeof(values), None, None):
                raise ctypes.WinError(ctypes.get_last_error())
            original = [os.get_handle_inheritable(handle) for handle in unique]
            try:
                for handle in unique:
                    os.set_handle_inheritable(handle, True)
                startup = Extended()
                startup.startup.cb = ctypes.sizeof(startup)
                startup.startup.lpDesktop = 'winsta0\\' + name
                startup.startup.dwFlags = 0x100 | 1
                startup.startup.wShowWindow = 0
                startup.startup.hStdInput, startup.startup.hStdOutput, startup.startup.hStdError = handles
                startup.attributes = ctypes.cast(attributes, ctypes.c_void_p)
                environment = dict(os.environ, **{_MARKER: name})
                block = ctypes.create_unicode_buffer('\0'.join(f'{k}={v}' for k,v in sorted(environment.items(),key=lambda item:item[0].upper())) + '\0')
                command = ctypes.create_unicode_buffer(subprocess.list2cmdline([sys.executable, *sys.argv]))
                flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS | 0x400 | 0x80000
                if not kernel.CreateProcessW(sys.executable, command, None, None, True, flags, block, os.getcwd(), ctypes.byref(startup), ctypes.byref(process)):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                for handle, inherited in zip(unique, original):
                    os.set_handle_inheritable(handle, inherited)
            kernel.CloseHandle(process.thread)
            process.thread = None
            if kernel.WaitForSingleObject(process.process, 0xffffffff) != 0:
                raise ctypes.WinError(ctypes.get_last_error())
            code = w.DWORD()
            if not kernel.GetExitCodeProcess(process.process, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            if desktop_name() != actual:
                raise RuntimeError('The parent build desktop changed unexpectedly.')
            return code.value
    finally:
        if process.thread: kernel.CloseHandle(process.thread)
        if process.process: kernel.CloseHandle(process.process)
        if attributes is not None: kernel.DeleteProcThreadAttributeList(attributes)
        user.CloseDesktop(desktop)
