"""Console + rotating file logging shared by the CLI, engine and dashboard."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from memecoin_trader.config import DATA_DIR, LOG_PATH


def setup_logging(level: int = logging.INFO) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if root.handlers:
        return  # already configured (e.g. re-entered from a subcommand)
    root.setLevel(level)

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=3)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
