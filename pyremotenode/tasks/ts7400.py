import logging
import re
import shlex
import subprocess as sp

from datetime import datetime, timedelta, time
from pyremotenode.tasks import BaseTask


class Sleep(BaseTask):
    def __init__(self, **kwargs):
        self._re_date = re.compile(r'^\d{8}$')
        super(Sleep, self).__init__(**kwargs)

    def default_action(self,
                       until_date="today",
                       until_time="0900",
                       **kwargs):
        logging.debug("Running default action for Sleep")
        dt = None

        if type(until_date) == str:
            if until_date.lower() == "today":
                dt = datetime.now().date()
            elif until_date.lower() == "tomorrow":
                dt = (datetime.now() + timedelta(days=1)).date()
            elif self._re_date.match(until_date):
                dt = datetime.strptime(until_date, "%d%m%Y").date()
            else:
                # TODO: Better handling to include datetime exceptions above
                raise NotImplementedError("Format for {} not implemented".format(until_date))
        else:
            raise TypeError("Error in type passed as argument {}".format(type(until_date)))

        tm = datetime.strptime(until_time, "%H%M").time()

        seconds = (datetime.combine(dt, tm) - datetime.now()).total_seconds()
        # Evaluate minimum seconds, push to tomorrow if we've gone past the time today
        if seconds < 60:
            dt = dt + timedelta(days=1)
            seconds = (datetime.combine(dt, tm) - datetime.now()).total_seconds()

        TS7400Utils.rtc_clock()
        logging.info("Sleeping for {} seconds".format(seconds))
        cmd = "tshwctl -L -m --timewkup={}".format(str(int(seconds)))

        logging.debug("Running Sleep command: {}".format(cmd))
        rc = sp.call(shlex.split(cmd))

        if rc != 0:
            # TODO: Handle this, should be extremely unlikely...
            self.state = BaseTask.CRITICAL
            logging.error("Did not manage to go to sleep, something's very wrong...")

        self.state = BaseTask.OK
        return self.state


class TS7400Utils(object):
    @staticmethod
    def rtc_clock(set = True):
        type = "S" if set else "G"
        logging.info("{}etting RTC from OS clock".format(type))
        cmd = "tshwctl --{}etrtc".format(type.lower())

        logging.debug("Running TS7400Utils command: {}".format(cmd))
        rc = sp.call(shlex.split(cmd))

        if rc != 0:
            logging.warning("Did not manage to {}et RTC...".format(type.lower()))