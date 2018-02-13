import datetime as dt
import logging
import logging.handlers
import os
import socket

from .config import Configuration

__all__ = ["Configuration"]


def setup_logging(name,
                  level=logging.DEBUG,
                  filelog=True,
                  logdir=os.path.join(os.sep, "data", "pyremotenode", "logs"),
                  syslog=False):
    hostname = socket.gethostname().split(".")[0]

    formatter = logging.Formatter(
        fmt="[{asctime} :{levelname:>10} {module:>20}] - {message}",
        datefmt="%d-%m-%y %T",
        style='{'
    )

    logging.basicConfig()
    log = logging.getLogger()
    log.setLevel(level)
    log.handlers = []

    stdout_log = logging.StreamHandler()
    stdout_log.setLevel(level)
    stdout_log.setFormatter(formatter)
    log.addHandler(stdout_log)

    if filelog:
        if not os.path.isdir(logdir):
            os.makedirs(logdir, exist_ok=True)

        file_hndlr = logging.FileHandler(os.path.join(logdir, dt.datetime.now().strftime("%d-%m-%Y.log")))
        file_hndlr.setLevel(level)
        file_hndlr.setFormatter(formatter)
        log.addHandler(file_hndlr)

    if syslog:
        log.warning("Syslog logging not yet implemented")