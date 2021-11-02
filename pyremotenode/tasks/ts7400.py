import logging
import os
import re
import shlex
import struct
import subprocess as sp
import time

from datetime import datetime, timedelta
from pyremotenode.tasks import BaseTask, ModemConnection


class Sleep(BaseTask):
    def __init__(self, **kwargs):
        self._re_date = re.compile(r'^\d{8}$')
        self._dt_format = "%d-%m-%Y"
        self._tm_format = "%H:%M:%S"
        super(Sleep, self).__init__(**kwargs)

    def default_action(self,
                       until_date="today",
                       until_time="0900",
                       modem_aware=False,
                       record_queue=None,
                       **kwargs):
        logging.debug("Running default action for Sleep")
        dt = None

        if type(until_date) == str:
            if until_date.lower() == "today":
                dt = datetime.utcnow().date()
            elif until_date.lower() == "tomorrow":
                dt = (datetime.utcnow() + timedelta(days=1)).date()
            elif self._re_date.match(until_date):
                dt = datetime.strptime(until_date, "%d%m%Y").date()
            else:
                # TODO: Better handling to include datetime exceptions above
                raise NotImplementedError("Format for {} not implemented".format(until_date))
        else:
            raise TypeError("Error in type passed as argument {}".format(type(until_date)))

        tm = datetime.strptime(until_time, "%H%M").time()

        modem_aware = bool(modem_aware)

        if modem_aware:
            logging.info("This sleep is modem aware and will wait until the task is finished")
            modem = ModemConnection()
            qsize = modem.message_queue.qsize()
            while qsize > 0:
                logging.warning("Deferring sleep for a minute to process queue size: {}".format(qsize))
                time.sleep(60)
                qsize = modem.message_queue.qsize()

        if record_queue:
            logging.debug("This sleep will record the SBD queue size at {}".format(record_queue))
            modem = ModemConnection()
            qsize = modem.message_queue.qsize()
            try:
                # If the queue size this time is > 0, accumulate in the file. If not, ensure the file is zero on write
                if os.path.exists(record_queue) and qsize > 0:
                    with open(record_queue, "rb") as fh:
                        prev_queue = struct.unpack("I", fh.read(4))[0]
                else:
                    prev_queue = 0

                with open(record_queue, "wb", buffering=0) as fh:
                    fh.write(struct.pack("I", qsize + prev_queue))
                    fh.flush()
            except (IOError, OSError):
                logging.exception("Could not write queue size before sleeping")

        seconds = (datetime.combine(dt, tm) - datetime.utcnow()).total_seconds()
        # Evaluate minimum seconds, push to tomorrow if we've gone past the time today
        if seconds < 60:
            dt = dt + timedelta(days=1)

        # Parse reboot time and previously set sleep duration / time at which it was set
        # TODO: The algorithm below is a bit shit, it would be better to incorporate the derived last_sleep_error
        # and its sources into the calculation, but it's late and keeping it simple for the moment is best
        # TODO: The reboot file needs to be POST-bootup time correction
        try:
            dt_reboot = self._get_reboot_time()
            dt_expected = datetime.combine(dt_reboot.date(), tm)
            dt_reboot_set, set_for = self._get_reboot_set_time()
            last_sleep_error = int((dt_reboot - (dt_reboot_set + timedelta(seconds=set_for))).total_seconds())
        except Exception:
            logging.exception("No satisfactory information to set adjustment offset")
            dt_reboot = None
            dt_expected = None
            dt_reboot_set = None

        prev_error = 0
        dt_to_wake = datetime.combine(dt, tm)
        dt_now = datetime.utcnow()

        # TODO: Because I don't trust this whole thing in relation to the RTC, this is a wee goody to retain the
        # previous years ability to just wake up earlier and sit idly waiting for the day to start
        if dt_reboot and dt_expected and dt_reboot_set and \
           not os.path.exists(os.path.expandvars(os.path.join("$HOME", "NO_SLEEP_ADJUST"))):
            prev_error = int((dt_reboot - dt_expected).total_seconds())
            logging.info("Sleep error rate has been calculated at {} seconds".format(prev_error))
            logging.info("The last sleep error was {}".format(last_sleep_error))

            if prev_error < 0:
                prev_error = 0
                logging.info("We aren't going to slow down the sleep yet, setting offset to zero")

        seconds = int((dt_to_wake - dt_now).total_seconds() - prev_error)

        TS7400Utils.rtc_clock()
        logging.info("Sleeping until {}, for {} seconds".format(
            dt_to_wake.strftime("{} {}".format(self._dt_format, self._tm_format)), seconds))

        cmd = "goto_sleep {} \"{}\"".format(
            seconds, dt_now.strftime("{} {}".format(self._dt_format, self._tm_format)))

        logging.info("Running Sleep command: {}".format(cmd))
        logging.shutdown()

        rc = sp.call(shlex.split(cmd))

        raise RuntimeError("No way in hell we should be reaching this point, sleep rc: {}".format(rc))

    def _get_reboot_time(self):
        path = os.path.expandvars(os.path.join("$HOME", "reboot.txt"))

        if os.path.exists(path) and \
                         (datetime.utcnow() - datetime.fromtimestamp(os.stat(path).st_mtime)).total_seconds() < 86400:
            with open(path, "r") as fh:
                line = fh.readline().strip()
        else:
            return None

        dt = self._parse_system_datetime(re.compile(r'^(.+)$'), line)
        logging.debug("Unit woke up at {}".format(
            dt.strftime("{} {}".format(self._dt_format, self._tm_format))))
        return dt

    def _get_reboot_set_time(self):
        path = os.path.expandvars(os.path.join("$HOME", "sleepinfo.txt"))

        # This only works with sleeping less than a day, which is what we want
        if os.path.exists(path) and \
                         (datetime.utcnow() - datetime.fromtimestamp(os.stat(path).st_mtime)).total_seconds() < 2 * 86400:
            with open(path, "r") as fh:
                line = fh.readline().strip()
                (secs, dt_str) = line.split(",")
            dt = datetime.strptime(dt_str, "{} {}".format(self._dt_format, self._tm_format))
            logging.debug("Unit was set to sleep at {} for {} seconds".format(dt_str, secs))
            return dt, int(secs)
        return None

    def _parse_system_datetime(self, regex, line):
        dt_match = regex.search(line)

        if dt_match:
            return datetime.strptime(dt_match.group(1), "{} {}".format(self._dt_format, self._tm_format))
        return None


class StatusUpdate(BaseTask):
    def __init__(self, **kwargs):
        super(StatusUpdate, self).__init__(**kwargs)

    def default_action(self, **kwargs):
        raise NotImplementedError("StatusUpdate not yet implemented")


class TS7400Utils(object):
    @staticmethod
    def rtc_clock(set=True):
        action_type = "s" if set else "g"
        logging.info("{}etting RTC from OS clock".format(action_type))
        cmd = "tshwctl --{}etrtc".format(action_type)

        logging.debug("Running TS7400Utils command: {}".format(cmd))
        rc = sp.call(shlex.split(cmd))

        if rc != 0:
            logging.warning("Did not manage to {}et RTC...".format(action_type))
