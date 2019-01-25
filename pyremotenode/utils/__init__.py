import datetime as dt
import logging
import logging.handlers
import os
import socket
import sys

from pyremotenode.utils.config import Configuration

__all__ = ["Configuration"]


def setup_logging(name='',
                  level=logging.INFO,
                  verbose=False,
                  logdir=os.path.join(os.sep, "data", "pyremotenode", "logs"),
                  logformat="[{asctime} :{levelname:>10} {module:>20}] - {message}",
                  syslog=False,
                  ):
    hostname = socket.gethostname().split(".")[0]

    formatter = logging.Formatter(
        fmt=logformat,
        datefmt="%d-%m-%y %T",
        style='{'
    )
    if verbose:
        level = logging.DEBUG

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.setLevel(level)

    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(level)

    if logdir:
        file_handler = logging.FileHandler(
            os.path.join(logdir, "{}{}{}.log".format(
                name,
                ("" if len(name) == 0 else "-"),
                dt.datetime.now().strftime("%Y-%m-%d"))))
        file_handler.setLevel(level)
        file_formatter = logging.Formatter(
            fmt='%(asctime)-25s%(levelname)-17s%(message)s',
            datefmt='%Y-%m-%dT%H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logging.getLogger().addHandler(file_handler)
