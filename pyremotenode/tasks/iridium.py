import logging
import shlex
import subprocess

from datetime import datetime

import pyremotenode.comms.iridium
from pyremotenode.comms.base import ModemConnection, ModemConnectionException
from pyremotenode.tasks import BaseTask
from pyremotenode.tasks.utils import CheckCommand
from pyremotenode.utils.config import Configuration


class BaseSender(BaseTask):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._modem = ModemConnection()
        logging.info("BaseSender has created {}".format(self.modem.instance.__class__.__name__))

    def default_action(self, **kwargs):
        raise NotImplementedError

    @property
    def modem(self):
        return self._modem


class ModemStarter(BaseSender):
    def default_action(self, **kwargs):
        self.modem.start()


class FileSender(BaseSender):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

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


class MessageSender(BaseSender):
    def __init__(self,
                 class_type=None,
                 message_length=None,
                 **kwargs):
        super().__init__(**kwargs)

        if class_type is not None and not isinstance(self.modem.instance, class_type):
            raise ModemConnectionException("Wrong type of modem connection: {}".format(self.modem.__class__.__name__))

        self._message_length = message_length

    def default_action(self, invoking_task, **kwargs):
        logging.debug("Running default_action for {}".format(self.__class__.__name__))

        if not invoking_task.binary:
            message_text = str(invoking_task.message)
            warning = True if message_text.find("warning") >= 0 else False
            critical = True if message_text.find("critical") >= 0 else False
        else:
            message_text = invoking_task.message
            warning = False
            critical = False

        msg = Message(
            message_text,
            binary=invoking_task.binary,
            include_date=not invoking_task.binary,
            warning=warning,
            critical=critical,
            max_length=self._message_length
        )
        self.modem.send_message(msg)
        self.modem.start()

    def send_message(self, message, include_date=True):
        logging.debug("Running send_message for {}".format(self.__class__.__name__))
        self.modem.send_message(Message(message,
                                        binary=self.binary,
                                        include_date=include_date,
                                        max_length=self._message_length))
        self.modem.start()

    @property
    def message_length(self):
        return self._message_length

    @message_length.setter
    def message_length(self, value):
        self._message_length = value


class IMTSender(MessageSender):
    def __init__(self,
                 binary=True,
                 class_type=pyremotenode.comms.iridium.CertusConnection,
                 critical=False,
                 include_date=False,
                 message_length=100000,
                 warning=False,
                 **kwargs):
        super().__init__(
            binary=True,
            class_type=pyremotenode.comms.iridium.CertusConnection,
            critical=critical,
            include_date=False,
            message_length=100000,
            warning=warning,
            **kwargs)


class SBDSender(MessageSender):
    def __init__(self, **kwargs):
        super().__init__(
            class_type=pyremotenode.comms.iridium.RudicsConnection,
            message_length=1920,
            **kwargs)

        if not self.modem.rockblock:
            self.message_length = 340


class Message:
    def __init__(self,
                 msg,
                 include_date=True,
                 warning=False,
                 critical=False,
                 binary=False,
                 max_length=None):
        self._msg = msg
        self._warn = warning
        self._critical = critical
        self._include_dt = include_date
        self._dt = datetime.utcnow()
        self._binary = binary
        self._max_length = max_length

    def get_message_text(self):
        if self._binary:
            logging.info("Got binary message: {} bytes (MAX: {})".format(len(self._msg), self._max_length))
            return self._msg if self._max_length is None else self._msg[:self._max_length]

        if self._include_dt:
            return "{}:{}".format(self._dt.strftime("%d-%m-%Y %H:%M:%S"),
                                  self._msg if self._max_length is None else self._msg[:self._max_length - 20])
        return self._msg if self._max_length is None else self._msg[:self._max_length]

    @property
    def binary(self):
        return self._binary

    @property
    def datetime(self):
        return self._dt

    def __lt__(self, other):
        return self.datetime < other.datetime


class MTMessageCheck(BaseTask):
    def __init__(self, **kwargs):
        super(MTMessageCheck, self).__init__(**kwargs)

    def default_action(self,
                       **kwargs):
        logging.debug("Running MTMessageCheck task")

        modem = ModemConnection()
        modem_locked = False

        qsize = modem.message_queue.qsize()
        if qsize > 0:
            logging.info("Abandoning MTMessageCheck as queue size is > 0, qsize = {}".format(qsize))
            return BaseTask.OK

        try:
            if modem.modem_lock.acquire(blocking=False):
                modem_locked = True
                logging.debug("Running MTMessageCheck initialisation")
                modem.initialise_modem()

                if modem.signal_check():
                    logging.debug("Running MTMessageCheck processing")
                    modem.process_sbd_message()
        except ModemConnectionException:
            logging.exception("Caught a modem exception running the regular task, abandoning")
        except Exception:
            logging.exception("Modem inoperational or another error occurred")
        finally:
            if modem_locked:
                modem.close()

                try:
                    modem.modem_lock.release()
                except RuntimeError:
                    logging.warning("Looks like the lock wasn't acquired, dealing with this...")

        return BaseTask.OK


class WakeupTask(CheckCommand):
    def __init__(self, **kwargs):
        BaseTask.__init__(self, **kwargs)
        self.modem = ModemConnection()

    def default_action(self, max_gap, **kwargs):
        ir_now = self.modem.get_system_time()

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