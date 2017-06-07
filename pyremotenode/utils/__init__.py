import logging
import logging.handlers
import socket

from .config import Configuration

__ALL__ = ["Configuration"]


def setup_logging(name, level=logging.DEBUG, syslog=False):
    """
    setup_logging

    This provides a standard logging setup for any caller, to both Syslog
    on the INFO channel and to STDERR

    Currently this assumes we have rsyslog running on the host in question

    :param name: Caller ID
    :param level: Logger level
    :return:
     logging.Logger usable object for caller
    """
    hostname = socket.gethostname().split(".")[0]

    # Take care of the STDERR configuration
    logging.basicConfig(
        level=level,
        format="[{asctime} :{levelname:>10} {module:>20}] - {message}",
        datefmt="%d-%m-%y %T",
        style='{',
    )

    if syslog:
        # Take care of the Syslog configuration
        syslog = logging.handlers.SysLogHandler(
            facility=logging.handlers.SysLogHandler.LOG_INFO
        )
        syslog.setLevel(level=level)
        syslog.setFormatter(logging.Formatter(
            fmt="{asctime} {hostname} [{{process}}]: {{message}}".format(hostname=hostname),
            datefmt="%h %d %T",
            style='{'
        ))

        logging.addHandler(syslog)
    return logging.getLogger(name)