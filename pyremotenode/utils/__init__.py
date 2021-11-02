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
                  logformat="[%(asctime)-20s :%(levelname)-8s] - %(message)s",
                  ):
    if verbose:
        level = logging.DEBUG

    if not os.path.exists(logdir):
        raise RuntimeError("{} must exist before running PyRemoteNode".
                           format(logdir))

    logging.basicConfig(
        level=level,
        format=logformat,
        datefmt="%d-%m-%y %T",
    )

    if logdir:
        file_handler = logging.handlers.TimedRotatingFileHandler(
            os.path.join(logdir, "{}.log".format(name)),
            when='midnight',
            utc=True
        )
        file_handler.setLevel(level)
        file_formatter = logging.Formatter(
            fmt='%(asctime)-25s%(levelname)-17s%(message)s',
            datefmt='%H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logging.getLogger().addHandler(file_handler)
