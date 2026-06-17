import logging


def get_logger(name: str = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"bilibili_monitor.{name}")
    return logging.getLogger("bilibili_monitor")


def setup_logging(level=logging.INFO, handler=None):
    logger = logging.getLogger("bilibili_monitor")
    logger.setLevel(level)
    if handler:
        logger.addHandler(handler)
