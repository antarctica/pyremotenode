import logging
import os
import re
import shlex
import subprocess as sp

from datetime import datetime, timedelta, time
from pyremotenode.tasks import BaseTask

from pyremotenode.tasks.iridium import SBDSender


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

        # Parse reboot time
        try:
            dt_reboot = self._get_reboot_time()
            dt_reboot_set = self._get_reboot_set_time()
        except Exception:
            logging.warning("No satisfactory information to set adjustment offset")
            dt_reboot = None
            dt_reboot_set = None

        reboot_diff = 0
        if dt_reboot and dt_reboot_set:
            reboot_diff = int((dt_reboot - dt_reboot_set).total_seconds())

            logging.debug("Difference between {} and {}: {} seconds".format(
                dt_reboot.strftime("%H:%M:%S"),
                dt_reboot_set.strftime("%H:%M:%S"),
                reboot_diff))

        TS7400Utils.rtc_clock()
        logging.info("Sleeping for {} seconds".format(seconds))
        iso_dt = datetime.combine(dt, tm)
        iso_dt.replace(microsecond=0)
        cmd = "gotosleep {} {}".format(str(int(seconds + reboot_diff)), datetime.isoformat(iso_dt))

        logging.debug("Running Sleep command: {}".format(cmd))
        rc = sp.call(shlex.split(cmd))

        if rc != 0:
            # TODO: Handle this, should be extremely unlikely...
            self.state = BaseTask.CRITICAL
            logging.error("Did not manage to go to sleep, something's very wrong...")

        self.state = BaseTask.OK
        return self.state

    def _get_reboot_time(self):
        path = os.path.expandvars(os.path.join("$HOME", "reboot.txt"))

        if os.path.exists(path) and \
                        (datetime.now() - datetime.fromtimestamp(os.stat(path).st_mtime)).total_seconds() < 86400:
            with open(path, "r") as fh:
                line = fh.readline().strip()
        else:
            return None

        dt = self._parse_system_datetime(re.compile(r'^Rebooted at (.+)$'), line)
        logging.debug("Unit was set to wake up at {}".format(datetime.isoformat(dt)))
        return dt

    def _get_reboot_set_time(self):
        path = os.path.expandvars(os.path.join("$HOME", "sleepinfo.txt"))

        if os.path.exists(path) and \
                        (datetime.now() - datetime.fromtimestamp(os.stat(path).st_mtime)).total_seconds() < 2 * 86400:
            with open(path, "r") as fh:
                line = fh.readline().strip()
                (secs, dt) = line.split(",")
            dt = datetime.strptime(dt, "%Y-%m-%dT%H:%M:%S")
            dt = dt + timedelta(seconds=int(secs))
            logging.debug("Unit was set to wake up at {}".format(datetime.isoformat(dt)))
            return dt
        return None

    def _parse_system_datetime(self, regex, line):
        dt_match = regex.search(line)

        if dt_match:
            return datetime.strptime(dt_match.group(1), "%a %b %d %H:%M:%S %Z %Y")
        return None


class StatusUpdate(BaseTask):
    def __init__(self, **kwargs):
        super(Sleep, self).__init__(**kwargs)

    def default_action(self, **kwargs):
        raise NotImplementedError("StatusUpdate not yet implemented")


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