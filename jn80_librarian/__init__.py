"""JN-80 terminal SysEx librarian."""

__version__ = "1.4.0"

__all__ = ["run", "__version__"]


def run() -> None:
	from .app import run as _run

	_run()
