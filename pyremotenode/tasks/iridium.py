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
from pyremotenode.utils.config import Configuration


class ModemLock(object):
    # TODO: Pass the configuration options for modem port (this is very LRAD specific)
    def __init__(self, dio_port="1_20"):
        self._lock = t.RLock()
        self._modem_port = dio_port

        cfg = Configuration().config
        self.grace_period = int(cfg['ModemConnection']['grace_period']) \
            if 'grace_period' in cfg['ModemConnection'] else 3

        self.offline_start = cfg['ModemConnection']['offline_start']
        self.offline_end = cfg['ModemConnection']['offline_end']

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
        tm.sleep(self.grace_period)
        return self._lock.release(**kwargs)

    def _in_offline_time(self):
        dt = datetime.now()
        start = datetime.combine(dt.date(), datetime.strptime(self.offline_start, "%H%M").time())
        end = datetime.combine(dt.date(), datetime.strptime(self.offline_end, "%H%M").time())
        res = start <= dt <= end
        logging.debug("Checking if {} is between {} and {}: {}".format(
            dt.strftime("%H:%M"), start.strftime("%H:%M"), end.strftime("%H:%M"), res))
        return res

    def __enter__(self):
        self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class ModemConnection(object):
    class __ModemConnection:
        _re_signal = re.compile(r'^\+CSQ:(\d)', re.MULTILINE)
        _re_sbdix_response = re.compile(r'^\+SBDIX:\s*(\d+), (\d+), (\d+), (\d+), (\d+), (\d+)', re.MULTILINE)

        def __init__(self):
            self._thread = None

            cfg = Configuration().config
            self.serial_port = cfg['ModemConnection']['serial_port']
            self.serial_timeout = cfg['ModemConnection']['serial_timeout']
            self.serial_baud = cfg['ModemConnection']['serial_baud']
            self.modem_wait = cfg['ModemConnection']['modem_wait']

            self._data = None

            self._modem_lock = ModemLock()       # Lock message buffer
            self._modem_wait = float(self.modem_wait)
            self._message_queue = queue.Queue()
            # TODO: This should be synchronized, but we won't really run into those issues with it as we never switch
            # the modem off whilst it's running
            self._running = False

            self.read_attempts = int(cfg['ModemConnection']['read_attempts']) \
                if 'read_attempts' in cfg['ModemConnection'] else 5

            logging.info("Ready to connect to modem on {}".format(self.serial_port))

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
                msg = None

                if not self.message_queue.empty() and self.modem_lock.acquire(blocking=False):
                    # TODO: This try-except is getting massive, would be nice to dissolve it at some point
                    try:
                        if not self._data:
                            logging.info("Creating pyserial comms instance to modem")
                            # Instantiation = opening of port hence why this is here and not in the constructor
                            self._data = serial.Serial(
                                port=self.serial_port,
                                timeout=float(self.serial_timeout),
                                write_timeout=float(self.serial_timeout),
                                baudrate=self.serial_baud,
                                bytesize=serial.EIGHTBITS,
                                parity=serial.PARITY_NONE,
                                stopbits=serial.STOPBITS_ONE
                            )
                            self._send_receive_messages("AT\r\n")
                            self._send_receive_messages("ATE0")
                            self._send_receive_messages("AT+SBDC")
                        else:
                            if not self._data.is_open:
                                logging.info("Opening existing modem serial connection")
                                self._data.open()
                            else:
                                raise ModemConnectionException("Modem appears to already be open, wasn't previously closed!?!")
                            # TODO: Turning off echo doesn't seem to work!?!
                            self._send_receive_messages("AT\r\n")
                            self._send_receive_messages("ATE0")

                        i = 1

                        # Check we have a good enough signal to work with (>3)
                        signal_test = self._send_receive_messages("AT+CSQ")
                        if signal_test == "":
                            raise ModemConnectionException(
                                "No response received for signal quality check")
                        signal_level = self._re_signal.search(signal_test)

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

                        logging.debug("Current queue size approx.: {}".format(str(self.message_queue.qsize())))

                        if type(signal_level) == int and signal_level >= 3:
                            while not self.message_queue.empty():
                                logging.debug("Still have messages, getting another...")
                                msg = self.message_queue.get(timeout=1)

                                if msg[0] == "sbd":
                                    text = msg[1].get_message_text().replace("\n", " ")

                                    response = self._send_receive_messages("AT+SBDWT={}".format(text))
                                    if response.split("\n")[-1] != "OK":
                                        raise ModemConnectionException("Error submitting message: {}".format(response))

                                    response = self._send_receive_messages("AT+SBDIX")
                                    if response.split("\n")[-1] != "OK":
                                        raise ModemConnectionException("Error submitting message: {}".format(response))

                                    (mo_status, mo_msn, mt_status, mt_msn, mt_len, mt_queued) = \
                                        self._re_sbdix_response.search(response).groups()
                                    # TODO: MT Queued, schedule download

                                    response = self._send_receive_messages("AT+SBDD0")
                                    if response.split("\n")[-1] == "OK":
                                        logging.debug("Message buffer cleared")

                                    if int(mo_status) > 2:
                                        raise ModemConnectionException("Failed to send message with MO Status: {}, breaking...".format(mo_status))

                                    # Don't reprocess this message goddammit!
                                    msg = None
                                else:
                                    raise ModemConnectionException("Invalid message type submitted {}".format(msg[0]))
                                i += 1
                        else:
                            logging.warning("Not enough signal to perform activities")
                    except ModemConnectionException:
                        logging.error("Out of logic modem operations, breaking to restart...")
                        logging.error(sys.exc_info())
                    except queue.Empty:
                        logging.info("{} messages processed, {} left in queue".format(i, self.message_queue.qsize()))
                    except Exception:
                        logging.error("Modem inoperational or another error occurred")
                        logging.error(sys.exc_info())
                    finally:
                        logging.info("Reached end of modem usage for this iteration...")
                        if msg:
                            self._message_queue.put(msg, timeout=5)
                        if self._data.is_open:
                            logging.debug("Closing modem serial connection")
                            self._data.close()

                        try:
                            self.modem_lock.release()
                        except RuntimeError:
                            logging.warning("Looks like the lock wasn't acquired, dealing with this...")
                logging.debug("{} thread waiting...".format(self.__class__.__name__))
                tm.sleep(self._modem_wait)

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
            self._data.flushOutput()
            self._data.write("{}\r".format(message.strip()).encode())

            logging.info('Message sent: "{}"'.format(message.strip()))

            # It seems possible that we don't get a response back sometimes, not sure why. Facilitate breaking comms
            # for another attempt in this case, else we'll end up in an infinite loop
            read_attempts = 0
            bytes_read = 0

            line = self._data.readline().decode('latin-1')
            logging.debug("Line received: '{}'".format(line.strip()))
            reply = line
            while line.rstrip() not in ["OK", "ERROR", "BUSY", "NO DIALTONE", "NO CARRIER", "RING", "NO ANSWER"]:
                line = self._data.readline().decode('latin-1').rstrip()
                bytes_read += len(line)
                logging.debug("Line received: '{}'".format(line))
                if len(line):
                    read_attempts == 0
                    reply += line + "\n"
                else:
                    read_attempts += 1
                    if read_attempts >= self.read_attempts:
                        logging.warning("We've read 0 bytes continuously on {} attempts, abandoning reads...".format(
                            self.read_attempts
                        ))
                        # It's up to the caller to handle this scenario, just give back what's available...
                        break

            reply = reply.strip()
            tm.sleep(0.2)
            logging.info('Response received: "{}"'.format(reply))

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

    # TODO: This should ideally deal with multiple modem instances based on parameterisation
    def __init__(self, **kwargs):
        logging.debug("ModemConnection constructor access")
        if not ModemConnection.instance:
            logging.debug("ModemConnection instantiation")
            ModemConnection.instance = ModemConnection.__ModemConnection(**kwargs)
        else:
            logging.debug("ModemConnection already instantiated")

    def __getattr__(self, item):
        return getattr(self.instance, item)


class RudicsConnection(BaseTask):
    class __RudicsConnection:
        def __init__(self,
                     device="ppp0",
                     max_checks=3,
                     max_kill_tries=5,
                     check_interval=20,
                     watch_interval=30,
                     wait_to_stop=10,
                     dialer="pppd",
                     **kwargs):
            self.modem = ModemConnection()

            logging.debug("Creating {0}".format(self.__class__.__name__))
            self._device = device
            self.re_ifip = re.compile(r'\s*inet \d+\.\d+\.\d+\.\d+')
            self._interface_path = os.path.join(os.sep, "proc", "sys", "net", "ipv4", "conf", self._device)
            self._proc = None
            self._thread = None
            self.running = False

            self.max_checks = int(max_checks)
            self.check_interval = int(check_interval)
            self.watch_interval = int(watch_interval)
            self.wait_to_stop = int(wait_to_stop)
            self.max_kill_tries = int(max_kill_tries)

            self.dialer = dialer

            if self.dialer not in ["wvdial", "pppd"]:
                raise RudicsConnectionException("Invalid modem type selected: {}".format(self.dialer))

        def start(self, **kwargs):
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
                    logging.info("Started {}".format(self.dialer))
            return success

        def ready(self):
            if self._proc and os.path.exists(self._interface_path):
                ipline = [x.strip() for x in
                          subprocess.check_output(["ip", "addr", "show", self._device], universal_newlines=True).split(
                              "\n")
                          if self.re_ifip.match(x)]

                if len(ipline) > 0:
                    logging.debug("Active interface detected with {0}".format(ipline[0]))
                    return True
                else:
                    logging.debug("Interface detected but no IP is present, so not ready to use...")
                    return False
            return False

        def watch(self):
            latched = False

            while self.running:
                rechecks = 1
                if not latched:
                    while not self.ready() \
                            and rechecks <= self.max_checks:
                        logging.debug(
                            "We have yet to get an interface up on check {0} of {1}".format(rechecks, self.max_checks))
                        tm.sleep(self.check_interval)
                        rechecks += 1

                    if rechecks >= self.max_checks \
                            and not self.ready():
                        logging.warning("We have failed to bring up the {0} interface".format(self._device))
                        # The Dialer process has failed to arrange an interface, we should kill the whole thing
                        self.running = False
                        self.stop()
                    else:
                        if not latched:
                            logging.info("We have the {0} interface at {1}".format(self._device, self._interface_path))
                        latched = True
                        self._run_ntpdate()
                        tm.sleep(self.watch_interval)
                else:
                    if not self.ready():
                        logging.info("Interface seems to have gone down, {} should attempt restart...".format(self.dialer))

                    tm.sleep(self.watch_interval)

        def stop(self, **kwargs):
            logging.debug("Running stop action for RudicsConnection")

            if self._proc:
                logging.info("Terminating process with PID {0}".format(self._proc.pid))
                self._proc.terminate()
                tm.sleep(self.wait_to_stop)
            self._terminate_dialer()

        def _start_dialer(self):
            logging.debug("Starting dialer and hoping it has a 'square go' at things...")

            if self.dialer == "pppd":
                cmd = shlex.split("pppd file /etc/ppp/peers/iridium")
            elif self.dialer == "wvdial":
                cmd = shlex.split("wvdial")
            else:
                raise RuntimeError("Cannot continue connecting, invalid dialer {} selected".format(self.dialer))

            self._proc = subprocess.Popen(cmd, universal_newlines=True)

            if self._proc.pid:
                logging.debug("We have a {} instance at pid {}".format(self.dialer, self._proc.pid))
                # TODO: Check for the existence / instantiation of the ppp child process, if we want to try more than once anyway
                return True
            logging.warning("We not not have a {} instance".format(self.dialer))
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

            while len(pids) and retries < self.max_kill_tries:
                if retries == self.max_kill_tries - 1:
                    sig_to_stop = signal.SIGKILL
                self._stop_dialer(sig_to_stop, pids)
                tm.sleep(self.wait_to_stop)
                retries += 1
                logging.debug("Attempt {0} to stop Dialer".format(retries))
                pids = self._dialer_pids()
                if not len(pids):
                    self.modem.modem_lock.release()
                    return True

            pppd_if_path = os.path.join("var", "run", "ppp0.pid")
            if os.path.exists(pppd_if_path):
                logging.warning("Unclean shutdown of pppd, removing PID file for interface")
                os.unlink(pppd_if_path)

            logging.error("We've not successfully killed pids {} but have to release the modem".format(",".join(pids)))
            self.modem.modem_lock.release()
            return False

        def _dialer_pids(self):
            logging.info("Checking for Dialer PIDs")
            dialer_pids = [y[0]
                           for y in [proc.split()
                                     for proc in
                                     subprocess.check_output(["ps", "-e"], universal_newlines=True).split('\n')
                                     if len(proc.split()) == 4]
                           if y[3].startswith(self.dialer)]

            logging.info("{} Dialer PIDs found: {}".format(len(dialer_pids), ",".join(dialer_pids)))
            return dialer_pids

        def _run_ntpdate(self):
            logging.info("Attempting to set system time from NTP")
            try:
                rc = subprocess.call(shlex.split("ntpdate time.nist.gov"))
            # Broad and dirty
            except subprocess.CalledProcessError:
                logging.error("Issue setting time using ntpdate: {}".format(sys.exc_info()))

            if rc != 0:
                logging.error("Non-zero return code calling ntpdate: {}".format(rc))

    instance = None

    def __init__(self, **kwargs):
        if not RudicsConnection.instance:
            BaseTask.__init__(self, **kwargs)
            RudicsConnection.instance = RudicsConnection.__RudicsConnection(**kwargs)

    def __getattr__(self, item):
        if hasattr(super(RudicsConnection, self), item):
            return getattr(super(RudicsConnection, self), item)
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
            warning=warning,
            critical=critical
        ))
        self.modem.start()

    def send_message(self, message, include_date=True):
        self.modem.send_sbd(SBDMessage(message, include_date=include_date))
        self.modem.start()


class SBDMessage(object):
    def __init__(self, msg, include_date=True, warning=False, critical=False):
        self._msg = msg
        self._warn = warning
        self._critical = critical

        if include_date:
            self._dt = datetime.now()
        else:
            self._dt = None

    def get_message_text(self):
        if self._dt:
            return "{}:{}".format(self._dt.strftime("%d-%m-%Y %H:%M:%S"), self._msg[:100])
        return "{}".format(self._msg)[:120]


class EmergencyConnection(RudicsConnection):
    def default_action(self, **kwargs):
        logging.warning("Creating an emergency connection: NOT IMPLEMENTED YET (TODO)")
        pass


class ModemConnectionException(Exception):
    pass


class RudicsConnectionException(Exception):
    pass
