import logging
import shlex
import subprocess
import threading as t
import time as tm
from datetime import datetime

from pyremotenode.utils import Configuration


class ModemLock(object):
    # TODO: Pass the configuration options for modem port (this is very LRAD specific)
    def __init__(self, dio_port="1_20"):
        self._lock = t.RLock()
        self._modem_port = dio_port

        cfg = Configuration().config
        self.grace_period = int(cfg['ModemConnection']['grace_period']) \
            if 'grace_period' in cfg['ModemConnection'] else 3

        self.offline_start = cfg['ModemConnection']['offline_start'] \
            if 'offline_start' in cfg['ModemConnection'] else None
        self.offline_end = cfg['ModemConnection']['offline_end'] \
            if 'offline_end' in cfg['ModemConnection'] else None

    def acquire(self, **kwargs):
        if self._in_offline_time():
            logging.info("Barring use of the modem during pre-determined window")
            return False

        logging.info("Acquiring and switching on modem {}".format(self._modem_port))
        res = self._lock.acquire(**kwargs)

        if res:
            cmd = "tshwctl --setdio {}".format(self._modem_port)
            rc = subprocess.call(shlex.split(cmd))
            logging.debug("tshwctl returned: {}".format(rc))

            if rc != 0:
                logging.warning("Non-zero acquisition command return value, releasing the lock!")
                self._lock.release(**kwargs)
                return False
            logging.debug("Sleeping for grace period of {} seconds to allow modem boot".format(self.grace_period))
            tm.sleep(self.grace_period)
        return res

    def release(self, **kwargs):
        logging.info("Releasing and switching off modem {}".format(self._modem_port))
        cmd = "tshwctl --clrdio {}".format(self._modem_port)
        rc = subprocess.call(shlex.split(cmd))
        logging.debug("tshwctl returned: {}".format(rc))
        # This doesn't need to be configurable, the DIO will be instantly switched off so we'll just give it a
        # second or two to avoid super-quick turnaround
        tm.sleep(2)
        return self._lock.release(**kwargs)

    def _in_offline_time(self):
        dt = datetime.utcnow()
        if self.offline_start and self.offline_end:
            start = datetime.combine(dt.date(), datetime.strptime(self.offline_start, "%H%M").time())
            end = datetime.combine(dt.date(), datetime.strptime(self.offline_end, "%H%M").time())
            res = start <= dt <= end
            logging.debug("Checking if {} is between {} and {}: {}".format(
                dt.strftime("%H:%M"), start.strftime("%H:%M"), end.strftime("%H:%M"), res))
        else:
            return False
        return res

    def __enter__(self):
        self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
