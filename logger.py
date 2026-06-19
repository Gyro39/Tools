"""
Structured logging for the BW ticket grabber.
"""
import logging
import sys
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"


def setup_logger(name="bw_ticket", log_dir=None):
    if log_dir is None:
        log_dir = LOG_DIR
    log_dir.mkdir(exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)-5s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(console)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_handler = logging.FileHandler(log_dir / f"bw_ticket_{timestamp}.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)s %(filename)s:%(lineno)d %(message)s"))
    logger.addHandler(file_handler)
    logger.info(f"Log file: {log_dir / f'bw_ticket_{timestamp}.log'}")
    return logger


def get_logger(name="bw_ticket"):
    return logging.getLogger(name)
