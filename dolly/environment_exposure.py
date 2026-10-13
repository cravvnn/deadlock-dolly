"""Reversible, readback-checked exposure-control experiment.

This verifies console state, not stable pixels or radiometric HDR. The effect
of these controls must be measured in a bounded capture before qualification.
"""
from contextlib import contextmanager
import math

from .convar_response import read_cvar_value
from .environment_capture import CaptureCancelled

CONTROLS = ('mat_tonemap_force_scale', 'mat_tonemap_force_min', 'mat_tonemap_force_max')
DIAGNOSTIC_CONTROLS = CONTROLS + ('mat_fullbright', 'mat_tonemap_debug')


def inspect_exposure(controller, *, cancelled=lambda: False):
    """Read candidate renderer controls without changing any setting.

    Unsupported/unparseable responses remain explicit unknowns. Successful
    console readback does not establish that a control affects captured pixels.
    Connection errors and lost ownership abort instead of returning stale state.
    """
    with controller._op_lock:
        controller._require_quiet_session('renderer exposure inspection')
        session = controller._session
        if session is None:
            raise RuntimeError('Exposure inspection requires the owned game session')
        def check():
            if cancelled():
                raise CaptureCancelled('Exposure inspection cancelled')
            if controller._session is not session:
                raise RuntimeError('Owned game session changed during exposure inspection')
        result = {}
        for name in DIAGNOSTIC_CONTROLS:
            check()
            response = controller._request(name, allow_error=True)
            check()
            try:
                value = read_cvar_value(name, response)
            except ValueError:
                value = None
            result[name] = dict(value=value, response=response, readable=value is not None)
        return dict(controls=result, pixel_exposure_verified=False)


def read_exposure(controller):
    return {name: read_cvar_value(name, controller._request(name)) for name in CONTROLS}


@contextmanager
def trial_exposure(controller, scale=1.0, *, cancelled=lambda: False):
    """Temporarily force a positive scale with min/max overrides disabled.

    Owns the controller operation lock and journals originals BEFORE writes so
    normal Stop/restore can recover a failed rollback. Never takes ownership of
    an already-pending exposure restoration. Caller must authorize live trials.
    """
    if type(scale) not in (float, int) or not math.isfinite(scale) or not 0.01 <= scale <= 8:
        raise ValueError('Exposure trial scale must be finite and between 0.01 and 8')
    def check():
        if cancelled():
            raise CaptureCancelled('Exposure trial cancelled')
    with controller._op_lock:
        check()
        controller._require_quiet_session('an exposure comparison')
        session = controller._session
        if session is None:
            raise RuntimeError('Exposure trial requires the owned game session')
        if any(name in controller._look_restore for name in CONTROLS):
            raise RuntimeError('Restore pending exposure controls before another trial')
        original = read_exposure(controller)
        check()
        desired = dict(zip(CONTROLS, (float(scale), -1.0, -1.0)))
        controller._look_restore.update(original)
        def require_session():
            if controller._session is not session:
                raise RuntimeError('Exposure session changed; original values remain pending')
            controller._require_connection()
        try:
            for name, value in desired.items():
                check()
                require_session()
                controller._request(f'{name} {value:.9g}')
            require_session()
            measured = read_exposure(controller)
            if measured != desired:
                raise RuntimeError('Game rejected or clamped the exposure controls')
            check()
            trial = dict(original=original, requested=desired, measured=measured,
                         pixel_exposure_verified=False)
            yield trial
            require_session()
            trial['measured_end'] = read_exposure(controller)
            if trial['measured_end'] != desired:
                raise RuntimeError('Exposure controls changed during the trial')
        finally:
            failures = []
            for name, value in original.items():
                try:
                    require_session()
                    controller._request(f'{name} {value:.17g}')
                    if read_cvar_value(name, controller._request(name)) != value:
                        raise RuntimeError('Restored value differs from original')
                    del controller._look_restore[name]
                except Exception as error:
                    failures.append(name+': '+str(error))
            if failures:
                raise RuntimeError('Exposure restoration incomplete: '+'; '.join(failures))
