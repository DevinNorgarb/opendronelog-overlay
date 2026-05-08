"""Compatibility shim for the pre-rebrand import path.

Prefer importing from `flightframe` going forward.
"""

from flightframe import __version__  # noqa: F401

__all__ = ["__version__"]
