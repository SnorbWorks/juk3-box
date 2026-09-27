"""Logging setup shared by every jb-* service.

Logs to stdout so `journalctl -u jb-<service>` is the single place to look;
no per-service log files.
"""

import logging
import sys


def setup(service_name: str) -> logging.Logger:
    logger = logging.getLogger(service_name)
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger
