import logging

__all__ = ["__version__"]

__version__ = "0.2.0"

logging.getLogger(__name__).addHandler(logging.NullHandler())
