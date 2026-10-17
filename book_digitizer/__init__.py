import os
import sys

# Ensure book_digitizer directory is in sys.path
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

__version__ = "1.0.0"
