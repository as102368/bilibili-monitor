import logging


def get_logger(name: str = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"bilibili_monitor.{name}")
    return logging.getLogger("bilibili_monitor")


def setup_logging(level=logging.INFO, handler=None):
    logger = logging.getLogger("bilibili_monitor")
    logger.setLevel(level)
    if handler:
        # 避免同类型 handler 重复添加导致日志重复输出
        existing_types = {type(h) for h in logger.handlers}
        if type(handler) not in existing_types:
            logger.addHandler(handler)
