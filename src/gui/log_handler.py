import logging
from PySide6.QtCore import QObject, Signal


class LogEmitter(QObject):
    log_signal = Signal(str)


class GuiLogHandler(logging.Handler):
    def __init__(self, emitter: LogEmitter):
        super().__init__()
        self.emitter = emitter
        self.setFormatter(logging.Formatter("%(asctime)s - %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        msg = self.format(record)
        self.emitter.log_signal.emit(msg)
