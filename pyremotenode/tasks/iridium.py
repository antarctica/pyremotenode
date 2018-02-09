import logging
import os
import queue
import re
import serial
import shlex
import signal
import subprocess
import sys
import threading as t
import time as tm

from datetime import datetime

from pyremotenode.tasks import BaseTask


class ModemLock(object):
    # TODO: Pass the configuration options for modem port
    def __init__(self, dio_port="1_20"):
        self._lock = t.RLock()
        self._modem_port = dio_port

    def acquire(self, **kwargs):
        logging.debug("Acquiring and switching on modem {}".format(self._modem_port))
        res = self._lock.acquire(**kwargs)
        tm.sleep(0.1)
        cmd = "tshwctl --setdio {}".format(self._modem_port)
        subprocess.call(shlex.split(cmd))
        return res

    def release(self, **kwargs):
        logging.debug("Releasing and switching off modem {}".format(self._modem_port))
        cmd = "tshwctl --clrdio {}".format(self._modem_port)
        subprocess.call(shlex.split(cmd))
        tm.sleep(0.1)
        return self._lock.release(**kwargs)

    def __enter__(self):
        self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class ModemConnection(object):
    class __ModemConnection:
        _re_signal = re.compile(r'\s*\+CSQ:(\d)\s*$', re.MULTILINE)
        _re_sbdix_response = re.compile(r'^\+SBDIX: (\d+), (\d+), (\d+), (\d+), (\d+), (\d+)', re.MULTILINE)

        # TODO: Pass configuration options to ModemConnection
        def __init__(self, serial_port="/tmp/ttySP1", serial_timeout=20):
            self._thread = None

            logging.info("Creating connection to modem on {}".format(serial_port))
            self._data = serial.Serial(
                port=serial_port,
                timeout=float(serial_timeout),
                baudrate=115200,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE
            )

            self._modem_lock = ModemLock()       # Lock message buffer
            self._message_queue = queue.Queue()
            # TODO: This should be synchronized potentially, but we won't really run into those issues with it
            self._running = False

        def start(self):
            # TODO: Draft implementation of the threading for message sending...
            if not self._thread:
                logging.info("Starting modem thread")
                self._thread = t.Thread(name=self.__class__.__name__, target=self.run)
                self._thread.setDaemon(True)
                self._running = True
                self._thread.start()

        def run(self):
            while self._running:
                if not self.message_queue.empty() and self.modem_lock.acquire(blocking=False):
                    if not self._data.is_open:
                        self._data.open()
                    i = 1

                    try:
                        # Check we have a good enough signal to work with (>3)
                        signal_test = self._send_receive_messages("AT+CSQ")
                        if signal_test == "":
                            raise ModemConnectionException(
                                "No response received for signal quality check")
                        signal_level = self._re_signal.match(signal_test)

                        if signal_level:
                            try:
                                signal_level = int(signal_level.group(1))
                                logging.debug("Got signal level {}".format(signal_level))
                            except ValueError:
                                raise ModemConnectionException(
                                    "Could not interpret signal from response: {}".format(signal_test))
                        else:
                            raise ModemConnectionException(
                                "Could not interpret signal from response: {}".format(signal_test))

                        if type(signal_level) == int and signal_level > 3:
                            msg = self.message_queue.get(block=False)

                            if msg[0] == "sbd":
                                text = msg[1].get_message_text().replace("\n", " ")

                                response = self._send_receive_messages("AT+SBDWT={}".format(text))
                                if response.rstrip().split("\n")[-1] != "OK":
                                    raise ModemConnectionException("Error submitting message: {}".format(response))

                                response = self._send_receive_messages("AT+SBDIX")
                                if response.rstrip().split("\n")[-1] != "OK":
                                    raise ModemConnectionException("Error submitting message: {}".format(response))

                                logging.info("Message sent: {}".format(response))
                                (mo_status, mo_msn, mt_status, mt_msn, mt_len, mt_queued) = \
                                    self._re_sbdix_response.search(response).groups()
                                # TODO: MT Queued, schedule download

                                response = self._send_receive_messages("AT+SBDD0")
                                if response.rstrip().split("\n")[-1] == "OK":
                                    logging.debug("Message buffer cleared")
                            else:
                                raise ModemConnectionException("Invalid message type submitted {}".format(msg[0]))
                            i += 1
                    except queue.Empty:
                        logging.info("{} messages processed".format(i))
                    except serial.serialutil.SerialException:
                        logging.error("Modem inoperational or another error occurred")
                        print(sys.exc_info())
                    finally:
                        if self._data.is_open:
                            self._data.close()
                        self.modem_lock.release()
                logging.debug("{} thread waiting...".format(self.__class__.__name__))
                tm.sleep(5)

        def _send_receive_messages(self, message):
            """
            send message through data port and recieve reply. If no reply, will timeout according to the
            data_timeout config setting

            python 3 requires the messages to be in binary format - so encode them, and also decode response.
            'latin-1' encoding is used to allow for sending file blocks which have bytes in range 0-255,
            whereas the standard or 'ascii' encoding only allows bytes in range 0-127

            readline() is used for most messages as it will block only until the full reply (a signle line) has been
            returned, or if no reply recieved, until the timeout. However, file_transfer_messages (downloading file
            blocks) may contain numerous newlines, and hence read() must be used (with an excessive upper limit; the
            maximum message size is ~2000 bytes), returning at the end of the configured timeout - make sure it is long enough!
            """
            if not self._data.isOpen():
                raise ModemConnectionException('Cannot send message; data port is not open')
            self._data.flushInput()
            self._data.write(("{}\n".format(message)).encode('latin-1'))

            logging.debug('Message sent: "{}"'.format(message.strip()))
            reply = self._data.readline().decode('latin-1')
            logging.debug('Message received: "{}"'.format(reply.strip()))
            return reply

        def send_sbd(self, message):
            # TODO: Better way of identifying transactions with modem?
            self.message_queue.put(("sbd", message))

        @property
        def modem_lock(self):
            return self._modem_lock

        @property
        def message_queue(self):
            return self._message_queue

    instance = None

    def __init__(self, **kwargs):
        if not self.instance:
            self.instance = ModemConnection.__ModemConnection(**kwargs)

    def __getattr__(self, item):
        return getattr(self.instance, item)


class RudicsConnection(BaseTask):
    class __RudicsConnection:
        def __init__(self,
                     device="ppp0",
                     max_checks=3,
                     check_interval=20,
                     watch_interval=30,
                     **kwargs):
            self.modem = ModemConnection()

            logging.debug("Creating {0}".format(self.__class__.__name__))
            self._device = device
            self.re_ifip = re.compile(r'\s*inet \d+\.\d+\.\d+\.\d+')
            self._interface_path = os.path.join(os.sep, "proc", "sys", "net", "ipv4", "conf", self._device)
            self._proc = None
            self._thread = None
            self.running = False

            self.max_checks = max_checks
            self.check_interval = check_interval
            self.watch_interval = watch_interval

        def start(self):
            logging.debug("Running start action for RudicsConnection")
            self._thread = t.Thread(name=self.__class__.__name__, target=self.watch)

            # We only need the exclusive use of the modem for Dialer, we don't actually use it via ModemConnection
            self.modem.modem_lock.acquire()

            logging.debug("Start comms, first checking for interface at {0}".format(self._interface_path))
            success = False

            if self.ready():
                logging.info("We have a ready to go {0} interface".format(self._device))
                # TODO: This would be an extremely weird situation, not necessarily successful
                success = True
            else:
                logging.info("We have no {0} interface".format(self._device))

                if self._start_dialer():
                    self.running = True
                    self._thread.start()
                    success = True
            return success

        def ready(self):
            if self._proc and os.path.exists(self._interface_path):
                ipline = [x.strip() for x in
                          subprocess.check_output(["ip", "addr", "show", self._device], universal_newlines=True).split(
                              "\n")
                          if self.re_ifip.match(x)]

                if len(ipline) > 0:
                    logging.info("Active interface detected with {0}".format(ipline[0]))
                    return True
                else:
                    logging.debug("Interface detected but no IP is present, so not ready to use...")
                    return False
            return False

        def watch(self):
            while self.running:
                rechecks = 1
                while not self.ready() \
                        and rechecks <= self.max_checks:
                    logging.debug(
                        "We have yet to get an interface up on check {0} of {1}".format(rechecks, self.max_checks))
                    tm.sleep(self.check_interval)
                    rechecks += 1

                if rechecks >= self.max_checks \
                        and not self.ready():
                    logging.warning("We have failed to bring up the {0} interface".format(self._device))
                    # TODO: The Dialer process has failed to arrange an interface, we should kill the whole thing
                    self.running = False
                    self.stop()
                else:
                    logging.info("We have the {0} interface at {1}".format(self._device, self._interface_path))
                    tm.sleep(self.watch_interval)

        def stop(self):
            logging.debug("Running stop action for RudicsConnection")

            if self._proc:
                logging.info("Terminating process with PID {0}".format(self._proc.pid))
                self._proc.terminate()
                self._proc = None
                tm.sleep(self._wait_to_stop)
            self._terminate_dialer()

        def _start_dialer(self):
            logging.debug("Starting dialer and hoping it has a \"square go\" at things...")
            self._proc = subprocess.Popen(shlex.split("pppd file /etc/ppp/peers/iridium"))

            # Dialer will eventually become a zombie if inactive
            # TODO: Cleanly terminate zombie processes more effectively allowing re-instantiation of comms (low priority)...
            if self._proc.pid:
                # TODO: Check for the existence / instantiation of the ppp child process, if we want to try more than once anyway
                return True
            return False

        def _stop_dialer(self, sig_to_stop, dialer_pids=[]):
            """
            We have no interface, kill dialer if it exists
            :return: Boolean
            """

            if len(dialer_pids):
                for pid in dialer_pids:
                    logging.debug("PID {0} being given {1}".format(pid, sig_to_stop))
                    os.kill(int(pid), sig_to_stop)

            return True

        def _terminate_dialer(self):
            retries = 0
            sig_to_stop = signal.SIGTERM

            pids = self._dialer_pids()
            if not len(pids):
                self.modem.modem_lock.release()
                return True

            while len(pids) and retries < self._max_kill_tries:
                if retries == self._max_kill_tries - 1:
                    sig_to_stop = signal.SIGKILL
                self._stop_dialer(sig_to_stop, pids)
                tm.sleep(self._wait_to_stop)
                retries += 1
                logging.debug("Attempt {0} to stop Dialer".format(retries))
                pids = self._dialer_pids()
                if not len(pids):
                    self.modem.modem_lock.release()
                    return True

            self.modem.modem_lock.release()
            return False

        def _dialer_pids(self):
            logging.info("Checking for Dialer PIDs")
            dialer_pids = [y[0]
                           for y in [proc.split()
                                     for proc in
                                     subprocess.check_output(["ps", "-e"], universal_newlines=True).split('\n')
                                     if len(proc.split()) == 4]
                           if y[3].startswith('pppd')]

            logging.info("{0} Dialer PIDs found...".format(len(dialer_pids)))
            return dialer_pids

    instance = None

    def __init__(self, **kwargs):
        if not RudicsConnection.instance:
            BaseTask.__init__(self, **kwargs)
            RudicsConnection.instance = RudicsConnection.__RudicsConnection(**kwargs)

    def __getattr__(self, item):
        return getattr(self.instance, item)


class SBDSender(BaseTask):
    def __init__(self, **kwargs):
        BaseTask.__init__(self, **kwargs)
        self.modem = ModemConnection()

    def default_action(self, invoking_task, **kwargs):
        logging.debug("Running default action for SBDMessage")

        message_text = str(invoking_task.state)
        warning = True if message_text.find("warning") >= 0 else False
        critical = True if message_text.find("critical") >= 0 else False

        self.modem.send_sbd(SBDMessage(
            message_text,
            warning,
            critical))
        self.modem.start()


class SBDMessage(object):
    def __init__(self, msg, warning=False, critical=False):
        self._msg = msg
        self._warn = warning
        self._critical = critical

        self._dt = datetime.now()

    def get_message_text(self):
        return "{}: {}".format(self._dt, self._msg)[:120]


class ModemConnectionException(Exception):
    pass