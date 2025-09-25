import logging
import shlex
import subprocess
import threading as t
import time as tm
from datetime import datetime

from pyremotenode.comms.base import ModemConnectionException
from pyremotenode.utils import Configuration


class ModemLock(object):
    def __init__(self):
        self._lock = t.RLock()

        cfg = Configuration().config
        self.grace_period = int(cfg['ModemConnection']['grace_period']) \
            if 'grace_period' in cfg['ModemConnection'] else 3

        if 'modem_power_dio' in cfg['ModemConnection']:
            raise ModemConnectionException("modem_power_dio is no longer used, please "
                                           "replace with modem_power_(on|off) commands")

        self._modem_power_on = cfg['ModemConnection']['modem_power_on'] if 'modem_power_on' in cfg['ModemConnection'] else None
        self._modem_power_off = cfg['ModemConnection']['modem_power_off'] if 'modem_power_off' in cfg['ModemConnection'] else None

        self.offline_start = cfg['ModemConnection']['offline_start'] \
            if 'offline_start' in cfg['ModemConnection'] else None
        self.offline_end = cfg['ModemConnection']['offline_end'] \
            if 'offline_end' in cfg['ModemConnection'] else None

    def acquire(self, **kwargs):
        if self._in_offline_time():
            logging.warning("Barring use of the modem during pre-determined window")
            return False

        res = self._lock.acquire(**kwargs)

        if res:
            rc = 0
            if self._modem_power_on is not None:
                logging.info("Switching on modem {}".format(self._modem_power_on))
                rc = subprocess.call(shlex.split(self._modem_power_on))
                logging.debug("Modem on rc: {}".format(rc))

            if rc != 0:
                logging.warning("Non-zero acquisition command return value, releasing the lock!")
                self._lock.release()
                return False
            logging.debug("Sleeping for grace period of {} seconds to allow modem boot".format(self.grace_period))
            tm.sleep(self.grace_period)
        return res

    def release(self):
        if self._modem_power_off is not None:
            logging.info("Switching off modem {}".format(self._modem_power_off))
            rc = subprocess.call(shlex.split(self._modem_power_off))
            logging.debug("Modem off rc: {}".format(rc))

            # This doesn't need to be configurable, the DIO will be instantly switched off so we'll just give it a
            # second or two to avoid super-quick turnaround
            tm.sleep(2)
        return self._lock.release()

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
