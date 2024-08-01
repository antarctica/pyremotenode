import logging
import shlex
import subprocess

from datetime import datetime

from pyremotenode.comms.iridium import ModemConnection
from pyremotenode.tasks import BaseTask
from pyremotenode.tasks.utils import CheckCommand


class BaseSender(BaseTask):
    def __init__(self, **kwargs):
        BaseTask.__init__(self, **kwargs)
        self.modem = ModemConnection()

    def default_action(self, **kwargs):
        raise NotImplementedError


class FileSender(BaseSender):
    def __init__(self, **kwargs):
        BaseSender.__init__(self, **kwargs)

    def default_action(self, invoking_task, **kwargs):
        logging.debug("Running default action for FileSender")

        if type(invoking_task.message) == list:
            logging.debug("Invoking tasks output is a list, goooooood")

            for f in invoking_task.message:
                # TODO: Wrap this in a function to hash and SBD the file?
                self.modem.send_file(f)
        else:
            logging.warning("File sender must be passed a task with output of a file list")
        self.modem.start()

    def send_file(self, filename):
        self.modem.send_file(filename)
        self.modem.start()


class SBDSender(BaseSender):
    def __init__(self, **kwargs):
        BaseSender.__init__(self, **kwargs)

    def default_action(self, invoking_task, **kwargs):
        logging.debug("Running default action for SBDSender")

        if not invoking_task.binary:
            message_text = str(invoking_task.message)
            warning = True if message_text.find("warning") >= 0 else False
            critical = True if message_text.find("critical") >= 0 else False
        else:
            message_text = invoking_task.message
            warning = False
            critical = False

        self.modem.send_sbd(SBDMessage(
            message_text,
            binary=invoking_task.binary,
            include_date=not invoking_task.binary,
            warning=warning,
            critical=critical
        ))
        self.modem.start()

    def send_message(self, message, include_date=True):
        self.modem.send_sbd(SBDMessage(message, include_date=include_date))
        self.modem.start()


class SBDMessage(object):
    def __init__(self, msg, include_date=True, warning=False, critical=False, binary=False):
        self._msg = msg
        self._warn = warning
        self._critical = critical
        self._include_dt = include_date
        self._dt = datetime.utcnow()
        self._binary = binary

    def get_message_text(self):
        if self._binary:
            logging.info("Returning binary message: {} bytes".format(len(self._msg)))
            return self._msg[:1920]

        if self._include_dt:
            return "{}:{}".format(self._dt.strftime("%d-%m-%Y %H:%M:%S"), self._msg[:1900])
        return "{}".format(self._msg)[:1920]

    @property
    def binary(self):
        return self._binary

    @property
    def datetime(self):
        return self._dt

    def __lt__(self, other):
        return self.datetime < other.datetime


class ModemConnectionException(Exception):
    pass

# ----------------------------


class WakeupTask(CheckCommand):
    def __init__(self, **kwargs):
        BaseTask.__init__(self, **kwargs)
        self.modem = ModemConnection()

    def default_action(self, max_gap, **kwargs):
        ir_now = self.modem.get_iridium_system_time()

        system_time_format = "%a %b %d %H:%M:%S %Z %Y"
        system_setformat = "%a %b %d %H:%M:%S UTC %Y"
        status = "ok - "
        output = ""
        change = ""

        dt = datetime.utcnow()
        output = "SysDT: {} ".format(dt.strftime("%d%m%Y %H%M%S"))

        if not ir_now:
            logging.warning("Unable to get Iridium time...")
            status = "critical - Unable to initiate Iridium to collect time"
        else:
            if ir_now:
                output += "IRDT: {}".format(ir_now.strftime("%d%m%Y %H%M%S"))
            else:
                status = "warning - "

            if (dt - ir_now).total_seconds() > int(max_gap):
                try:
                    rc = subprocess.call(shlex.split("date -s '{}'".format(
                                         ir_now.strftime(system_setformat))))
                except Exception:
                    logging.warning("Could not set system time to Iridium time")
                    status = "critical -"
                    change = "Cannot set SysDT"
                else:
                    logging.info("Changed system time {} to {}".format(
                        dt.strftime("%d-%m-%Y %H:%M:%S"),
                        ir_now.strftime("%d-%m-%Y %H:%M:%S")
                    ))
                    change = "SysDT set to GPSDT"
            else:
                logging.info("Iridium time {} and system time {} within acceptable difference of {}".format(
                    ir_now.strftime("%d-%m-%Y %H:%M:%S"), dt.strftime("%d-%m-%Y %H:%M:%S"), max_gap))
                change = "OK"

        self._output = (" ".join([status, output, change])).strip()
        return self._process_cmd_output(self._output)
