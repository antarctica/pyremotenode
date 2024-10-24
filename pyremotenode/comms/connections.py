from abc import ABCMeta, abstractmethod
import logging
import os
import queue
import re
import threading as t
import time as tm
import traceback
from datetime import datetime

from pyremotenode.comms.utils import ModemLock

import serial


class ConnectionException(Exception):
    pass


class BaseConnection(metaclass=ABCMeta):
    """ Connection interface for communications - usable by tasks et al

    This is hopefully fairly compatible for any Hayes compatible device
    Expose a common interface of for communications functionality as well as handling
    some of the common things:
        - RS232 comms to the modems
        - Thread safety and execution
        - Message queueing and communication type handling
        - Failure handling for registration, failed sends and suchlike
        - Configuration management for subclasses
    """
    priority_message_mo = 1
    priority_file_mo = 2

    re_modem_resp = re.compile(b"""(OK
                                    |ERROR
                                    |BUSY
                                    |NO\ DIALTONE
                                    |NO\ CARRIER
                                    |RING
                                    |NO\ ANSWER
                                    |READY
                                    |GOFORIT
                                    |NAMERECV
                                    |CONNECT(?:\s\d+)?)
                                    [\r\n]*$""", re.X)
    re_signal = re.compile(r'^\+CSQ: *(?:[\-+\d]+,)?(\d)', re.MULTILINE)

    def __init__(self, cfg, *args, **kwargs):
        self._thread = None

        self.serial_port = cfg['ModemConnection']['serial_port']
        self.serial_timeout = cfg['ModemConnection']['serial_timeout']
        self.serial_baud = cfg['ModemConnection']['serial_baud']
        self._modem_wait = cfg['ModemConnection']['modem_wait']

        self._data_conn = None
        self._dataxfer_errors = 0
        self._message_queue = queue.PriorityQueue()
        self._modem_lock = ModemLock()
        self._modem_wait = float(self._modem_wait)
        # TODO: This should be synchronized, but we won't really run into those issues with it as we never switch
        #  the modem off whilst it's running
        self._running = False
        self._thread = None
        self._thread_lock = t.Lock()       # Lock thread creation

        # Process out behavioural settings from the configuration
        # TODO: this can be made much more concise using a Configuration getter with defaults
        # TODO: there should be some accessors for these as properties
        self.msg_timeout = float(cfg['ModemConnection']['msg_timeout']) \
            if 'msg_timeout' in cfg['ModemConnection'] else 20.0
        self.msg_xfer_timeout = float(cfg['ModemConnection']['msg_xfer_timeout']) \
            if 'msg_xfer_timeout' in cfg['ModemConnection'] else 60.0
        self.msg_wait_period = float(cfg['ModemConnection']['msg_wait_period']) \
            if 'msg_wait_period' in cfg['ModemConnection'] else 1.0

        self.max_reg_checks = int(cfg['ModemConnection']['max_reg_checks']) \
            if 'max_reg_checks' in cfg['ModemConnection'] else 6
        self.min_signal_level = int(cfg['ModemConnection']['min_signal_level']) \
            if 'min_signal_level' in cfg['ModemConnection'] else 3
        self.poll_periodically = bool(cfg['ModemConnection']['poll_periodically']) \
            if 'poll_periodically' in cfg['ModemConnection'] else False
        self.reg_check_interval = float(cfg['ModemConnection']['reg_check_interval']) \
            if 'reg_check_interval' in cfg['ModemConnection'] else 10
        self.mt_destination = cfg['ModemConnection']['mt_destination'] \
            if 'mt_destination' in cfg['ModemConnection'] else (
            os.path.join(os.sep, "data", "pyremotenode", "messages"))

        self.msg_attempts = int(cfg['ModemConnection']['msg_attempts']) \
            if 'msg_attempts' in cfg['ModemConnection'] else 3
        self.msg_gap = int(cfg['ModemConnection']['msg_gap']) \
            if 'msg_gap' in cfg['ModemConnection'] else 1

        self.terminator = "\r"

        if not os.path.exists(self.mt_destination):
            logging.info("Creating non-existent message destination: {}".format(self.mt_destination))
            os.makedirs(self.mt_destination, exist_ok=True)

        logging.debug("Creating {}".format(self.__class__.__name__))

    def close(self):
        if self.data_conn and self.data_conn.is_open:
            logging.debug("Closing and removing modem serial connection")
            self.data_conn.close()

    @abstractmethod
    def get_system_time(self):
        raise NotImplementedError("get_system_time not implemented")

    def initialise_modem(self):
        """

        Opens the serial interface to the modem and performs the necessary registration
        checks for activity on the network. Raises an exception if we can't gather a
        suitable connection

        :return: None
        """
        if not self.data_conn:
            logging.info("Creating pyserial comms instance to modem")
            # Instantiation = opening of port hence why this is here and not in the constructor
            self.data_conn = serial.Serial(
                port=self.serial_port,
                timeout=float(self.serial_timeout),
                write_timeout=float(self.serial_timeout),
                baudrate=self.serial_baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
            )
        else:
            if not self.data_conn.is_open:
                logging.info("Opening existing modem serial connection")
                self.data_conn.open()
            # TODO: Shared object now between threads, at startup, don't think this needs to be present
            else:
                logging.warning("Modem appears to already be open, wasn't previously closed!?!")

        # TODO: The method for registration check could be abstracted, but probably unnecessary - review

    def modem_command(self,
                      message,
                      raw=False,
                      dont_decode=False,
                      timeout_override=None):
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
        if self.data_conn is None or not self.data_conn.is_open:
            raise ConnectionException('Cannot send message; data port is not open')
        self.data_conn.flushInput()
        self.data_conn.flushOutput()

        if not raw:
            self.data_conn.write("{}{}".format(message.strip(), self.terminator).encode("latin-1"))
            logging.info('Message sent: "{}"'.format(message.strip()))
        else:
            self.data_conn.write(message)
            logging.debug("Binary message of length {} bytes sent".format(len(message)))

        # It seems possible that we don't get a response back sometimes, not sure why. Facilitate breaking comms
        # for another attempt in this case, else we'll end up in an infinite loop
        bytes_read = 0

        reply = bytearray()
        modem_response = False
        start = datetime.utcnow()

        msg_timeout = self.msg_timeout
        if timeout_override:
            msg_timeout = timeout_override

        while not modem_response:
            tm.sleep(0.1)
            reply += self.data_conn.read_all()
            bytes_read += len(reply)

            duration = (datetime.utcnow() - start).total_seconds()
            if not len(reply):
                if duration > msg_timeout:
                    logging.warning("We've read 0 bytes continuously for {} seconds, abandoning reads...".format(
                        duration
                    ))
                    # It's up to the caller to handle this scenario, just give back what's available...
                    raise ConnectionException("Response timeout from serial line...")
                else:
                    # logging.debug("Waiting for response...")
                    tm.sleep(self.msg_wait_period)
                    continue

            start = datetime.utcnow()
            if not dont_decode:
                logging.debug("Reply received: '{}'".format(reply.decode().strip()))

            cmd_match = self.re_modem_resp.search(reply.strip())
            if cmd_match:
                tm.sleep(0.1)
                if not self.data_conn.in_waiting:
                    modem_response = True

        if dont_decode:
            logging.info("Response of {} bytes received".format(bytes_read))
            # with open("test2.{}.msg".format(bytes_read), "wb") as fh:
            #    fh.write(reply)
        else:
            reply = reply.decode().strip()
            logging.info('Response received: "{}"'.format(reply))

        return reply

    def output_recv_message(self,
                            recv_msg_id,
                            recv_msg_len,
                            payload,
                            calcd_chksum,
                            recv_chksum):
        valid = False
        if recv_msg_len != len(payload):
            logging.warning("Message length indicated {} is not the same as actual message: {}".format(
                recv_msg_len, len(payload)
            ))
        elif calcd_chksum != recv_chksum:
            logging.warning("Message checksum {} is not the same as calculated checksum: {}".format(
                calcd_chksum, recv_chksum
            ))
        else:
            valid = True

        msg_dt = datetime.utcnow().strftime("%d%m%Y%H%M%S")
        msg_path = self.mt_destination if valid else os.path.join(self.mt_destination, "invalid")
        os.makedirs(msg_path, exist_ok=True)
        msg_filename = os.path.join(
            msg_path,
            "{}_{}.{}".format(recv_msg_id, msg_dt, "msg" if valid else "bak"))
        logging.info("Received MT message, outputting to {}".format(msg_filename))

        try:
            with open(msg_filename, "wb") as fh:
                fh.write(payload)
        except (OSError, IOError):
            logging.error("Could not write {}, abandoning...".format(payload))

    def poll_for_messages(self):
        pass

    @abstractmethod
    def process_message(self, msg):
        pass

    def process_outstanding_messages(self):
        """
        Process the remains of the queue in the order SBD MO, file transfers

        We undertake the SBD first, as they're quicker and usually going to be used for key data. The SBD method
        will also check the MT SBD queue with Iridium which will pull down last, so we know all data is out before
        somebody messes with the configuration remotely

        :return: Number of messages processed
        """
        logging.debug("Processing currently queued messages...")
        while not self.message_queue.empty():
            msg = self.message_queue.get(timeout=1)
            try:
                if msg[0] == self.priority_message_mo:
                    self.process_message(msg[1])
                elif msg[0] == self.priority_file_mo:
                    # TODO: We need to batch file transfers together into
                    #  a single long running call
                    self.process_transfer(msg[1])
                else:
                    raise ConnectionException("Invalid message type submitted {}".format(msg[0]))
            except ConnectionException:
                # TODO: We need to put this back at the start of the queue, not the end...
                logging.warning("Failed message handling, putting back to the queue...")
                self.message_queue.put(msg)
                raise

    @abstractmethod
    def process_transfer(self, filename):
        pass

    def run(self):
        # TODO: this needs a refactor now that polling and processing are separated
        while self.running:
            # TODO: this was written a long time ago and smells slightly, why are we independently
            #  tracking the status of a re-entrant lock?
            modem_locked = False

            try:
                if not self.message_queue.empty():
                    if self.modem_lock.acquire(blocking=False):
                        modem_locked = True
                        self.initialise_modem()

                        if not self.message_queue.empty():
                            logging.debug("Current queue size approx.: {}".format(str(self.message_queue.qsize())))

                            if self.signal_check(self.min_signal_level):
                                num = self.process_outstanding_messages()
                                logging.info("Processed {} outgoing messages".format(num if num is not None else 0))

                                self.poll_for_messages()
                            else:
                                logging.warning("Not enough signal to perform activities")
                    else:
                        logging.warning("Unable to acquire the modem lock, abandoning for the mo")
                else:
                    # TODO: this is were we could respond to unsolicited messaging from modem
                    if self.poll_periodically and self.modem_lock.acquire(blocking=False):
                        modem_locked = True
                        self.initialise_modem()

                        if self.signal_check(self.min_signal_level):
                            logging.debug("Polling modem for messages")
                            self.poll_for_messages()

                logging.info("Reached end of modem usage for this iteration...")
            except ConnectionException:
                logging.error("Out of logic modem operations, breaking to restart...")
                logging.error(traceback.format_exc())
            except queue.Empty:
                logging.info("{} messages processed, {} left in queue".format(num, self.message_queue.qsize()))
            except Exception:
                logging.error("Modem inoperational or another error occurred")
                logging.error(traceback.format_exc())
            finally:
                if modem_locked:
                    self.close()

                    try:
                        self.modem_lock.release()
                    except RuntimeError:
                        logging.warning("Looks like the lock wasn't acquired, dealing with this...")

            logging.debug("{} thread waiting...".format(self.__class__.__name__))
            tm.sleep(self._modem_wait)

    def send_file(self, file, timeout=None):
        self.message_queue.put((self.priority_file_mo, file))

    def send_message(self, message, timeout=None):
        self.message_queue.put((self.priority_message_mo, message))

    def signal_check(self,
                     min_signal=3):
        """Check what the current signal is at present

        Issue commands to the modem to evaluate the signal strength currently available

        :param min_signal: The minimum allowed signal for a positive result
        :return: boolean: True if signal checks OK, False otherwise
        """

        # Check we have a good enough signal to work with (>3)
        signal_test = self.modem_command("AT+CSQ?")
        if signal_test == "":
            raise ConnectionException(
                "No response received for signal quality check")
        signal_level = self.re_signal.search(signal_test)

        if signal_level:
            try:
                signal_level = int(signal_level.group(1))
                logging.debug("Got signal level {}".format(signal_level))
            except ValueError:
                raise ConnectionException(
                    "Could not interpret signal from response: {}".format(signal_test))
        else:
            raise ConnectionException(
                "Could not interpret signal from response: {}".format(signal_test))

        if type(signal_level) == int and signal_level >= min_signal:
            return True
        return False

    def start(self):
        with self.thread_lock:
            if not self._thread:
                logging.info("Starting modem thread")
                self._thread = t.Thread(name=self.__class__.__name__, target=self.run)
                self._thread.setDaemon(True)
                self._running = True
                self._thread.start()

    @property
    def data_conn(self):
        return self._data_conn

    @data_conn.setter
    def data_conn(self, data_conn):
        """

        Args:
            data_conn: always needs to be a serial interface of some kind
        """
        self._data_conn = data_conn

    @property
    def message_queue(self):
        return self._message_queue

    @property
    def modem_lock(self):
        return self._modem_lock

    @property
    def running(self):
        return self._running

    @property
    def thread_lock(self):
        return self._thread_lock


