"""Central logging; artists receive concise operator reports."""

import logging

logger = logging.getLogger("open_painter")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("Open Painter: %(levelname)s: %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
