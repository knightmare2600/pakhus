"""Logging setup. Per plan.txt: log everything to /var/log/pakhus/install.log,
in addition to stdout (which pakhus-installer.service already sends to both the
journal and the console).
"""
import logging
import os

LOG_PATH = "/var/log/pakhus/install.log"


def setup():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

    logger = logging.getLogger("pakhus")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    file_handler = logging.FileHandler(LOG_PATH)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("[pakhus] %(message)s"))
    logger.addHandler(console_handler)

    return logger
