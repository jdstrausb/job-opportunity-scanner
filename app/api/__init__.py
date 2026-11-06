"""REST bridge exposing the scanner's data and controls to the web UI."""

from .app import create_app

__all__ = ["create_app"]
