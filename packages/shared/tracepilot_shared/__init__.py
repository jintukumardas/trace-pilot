"""tracepilot_shared — the canonical type + config contract for TracePilot.

Every other package depends on this one. Import models from
``tracepilot_shared.models`` and configuration from ``tracepilot_shared.config``.
"""

from . import ids
from .config import Settings, get_settings
from .logging import configure_logging, get_logger

__version__ = "0.1.0"

__all__ = ["ids", "Settings", "get_settings", "configure_logging", "get_logger", "__version__"]
