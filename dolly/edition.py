"""Explicit executable edition, selected before the desktop imports settings."""
_environment = False


def configure_environment():
    global _environment
    _environment = True


def is_environment():
    return _environment


def application_name():
    return 'Dolly Environment Capture' if _environment else 'Deadlock Dolly'


def settings_directory():
    return 'DollyEnvironmentCapture' if _environment else 'DeadlockDolly'


def allow_standard_updates():
    return not _environment
