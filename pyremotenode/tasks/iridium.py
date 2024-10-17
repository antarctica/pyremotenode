import binascii
import logging
import os
import queue
import re
import serial
import shlex
import stat
import struct
import subprocess
import threading as t
import time as tm
import traceback
import xmodem

from datetime import datetime, timedelta

from pyremotenode.tasks import BaseTask
from pyremotenode.tasks.utils import CheckCommand
from pyremotenode.utils.config import Configuration

# TODO: Major refactor as the serial comms have undermined the original layout for this module
# TODO: We need to implement a shared key security system on the web-exposed service
# TODO: This whole implementation is intrisincally tied to the TS7400


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


class ModemConnection(object):
    class __ModemConnection:
        _re_signal = re.compile(r'^\+CSQ:(\d)', re.MULTILINE)
        _re_sbdix_response = re.compile(r'^\+SBDIX:\s*(\d+), (\d+), (\d+), (\d+), (\d+), (\d+)', re.MULTILINE)
        _re_creg_response = re.compile(r'^\+CREG:\s*(\d+),\s*(\d+),?.*', re.MULTILINE)
        _re_msstm_response = re.compile(r'^\-MSSTM: ([0-9a-f]{8}).*', re.MULTILINE | re.IGNORECASE)
        _re_modem_resp = re.compile(b"""(OK
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

        _priority_sbd_mo = 1
        _priority_file_mo = 2
        _priority_sbd_mt = 3

        def __init__(self):
            self._thread = None

            cfg = Configuration().config
            self.serial_port = cfg['ModemConnection']['serial_port']
            self.serial_timeout = cfg['ModemConnection']['serial_timeout']
            self.serial_baud = cfg['ModemConnection']['serial_baud']
            self.modem_wait = cfg['ModemConnection']['modem_wait']
            self.modem_power = cfg['ModemConnection']['modem_power_dio'] \
                if 'modem_power_dio' in cfg['ModemConnection'] else None

            self._data = None
            self._dataxfer_errors = 0

            self._modem_lock = ModemLock(self.modem_power) if self.modem_power else ModemLock()
            self._thread_lock = t.Lock()       # Lock thread creation
            self._modem_wait = float(self.modem_wait)
            self._message_queue = queue.PriorityQueue()
            self._mt_queued = False
            # TODO: This should be synchronized, but we won't really run into those issues with it as we never switch
            # the modem off whilst it's running
            self._running = False

            self.msg_timeout = float(cfg['ModemConnection']['msg_timeout']) \
                if 'msg_timeout' in cfg['ModemConnection'] else 20.0
            self.sbd_xfer_timeout = float(cfg['ModemConnection']['sbd_xfer_timeout']) \
                if 'sbd_xfer_timeout' in cfg['ModemConnection'] else 60.0
            self.msg_wait_period = float(cfg['ModemConnection']['msg_wait_period']) \
                if 'msg_wait_period' in cfg['ModemConnection'] else 1.0

            self.max_reg_checks = int(cfg['ModemConnection']['max_reg_checks']) \
                if 'max_reg_checks' in cfg['ModemConnection'] else 6
            self.reg_check_interval = float(cfg['ModemConnection']['reg_check_interval']) \
                if 'reg_check_interval' in cfg['ModemConnection'] else 10
            self.mt_destination = cfg['ModemConnection']['mt_destination'] \
                if 'mt_destination' in cfg['ModemConnection'] else os.path.join(
                    os.sep, "data", "pyremotenode", "messages")

            self.sbd_attempts = int(cfg['ModemConnection']['sbd_attempts']) \
                if 'sbd_attempts' in cfg['ModemConnection'] else 3
            self.sbd_gap = int(cfg['ModemConnection']['sbd_gap']) \
                if 'sbd_gap' in cfg['ModemConnection'] else 1

            # Defeats https://github.com/pyserial/pyserial/issues/59 with socat usage
            self.virtual = bool(cfg['ModemConnection']['virtual']) \
                if 'virtual' in cfg['ModemConnection'] else False
            # Allows adaptation to Rockblocks reduced AT command set and non-Hayes line endings
            self.rockblock = bool(cfg['ModemConnection']['rockblock']) \
                if 'rockblock' in cfg['ModemConnection'] else False

            # MO dial up vars
            self.dialup_number = cfg['ModemConnection']['dialup_number'] \
                if 'dialup_number' in cfg['ModemConnection'] else None
            self._call_timeout = cfg['ModemConnection']['call_timeout'] \
                if "call_timeout" in cfg['ModemConnection'] else 120

            self._lineend = "\r"
            if self.virtual or self.rockblock:
                self._lineend = "\n"

            if not os.path.exists(self.mt_destination):
                logging.info("Creating non-existent message destination: {}".format(self.mt_destination))
                os.makedirs(self.mt_destination, exist_ok=True)

            logging.info("Ready to connect to modem on {}".format(self.serial_port))

        def close(self):
            if self._data and self._data.is_open:
                logging.debug("Closing and removing modem serial connection")
                self._data.close()
            self._data = None

        def get_iridium_system_time(self):
            with self._thread_lock:
                logging.debug("Getting Iridium system time")
                now = 0
                # Iridium epoch is 11-May-2014 14:23:55 (currently, IT WILL CHANGE)
                ep = datetime(2014, 5, 11, 14, 23, 55)
                locked = False

                try:
                    locked = self.modem_lock.acquire()
                    if locked:
                        self.initialise_modem()

                        # And time is measured in 90ms intervals eg. 62b95972
                        result = self._send_receive_messages("AT-MSSTM")
                        if result.splitlines()[-1] != "OK":
                            raise ModemConnectionException("Error code response from modem, cannot continue")

                        result = self._re_msstm_response.match(result).group(1)

                        now = timedelta(seconds=int(result, 16) / (1. / 0.09))
                    else:
                        return None
                except (ModemConnectionException, serial.SerialException, serial.SerialTimeoutException):
                    logging.exception("Cannot get Iridium time")
                    return False
                except IndexError:
                    logging.exception("Something likely went wrong initialising the modem")
                    return False
                except ValueError:
                    logging.exception("Cannot use value for Iridium time")
                    return False
                except TypeError:
                    logging.exception("Cannot cast value for Iridium time")
                    return False
                finally:
                    if locked:
                        self.modem_lock.release()
                return now + ep

        def start(self):
            with self._thread_lock:
                if not self._thread:
                    logging.info("Starting modem thread")
                    self._thread = t.Thread(name=self.__class__.__name__, target=self.run)
                    self._thread.setDaemon(True)
                    self._running = True
                    self._thread.start()

        def run(self):
            while self._running:
                modem_locked = False
                num = 0

                try:
                    if not self.message_queue.empty() \
                            and self.modem_lock.acquire(blocking=False):
                        modem_locked = True

                        self.initialise_modem()

                        if not self.message_queue.empty():
                            logging.debug("Current queue size approx.: {}".format(str(self.message_queue.qsize())))

                            if self.signal_check():
                                num = self._process_outstanding_messages()
                                logging.info("Processed {} outgoing messages".format(num if num is not None else 0))
                            else:
                                logging.warning("Not enough signal to perform activities")
                        logging.info("Reached end of modem usage for this iteration...")
                except ModemConnectionException:
                    logging.exception("Out of logic modem operations, breaking to restart...")
                except queue.Empty:
                    logging.info("{} messages processed, {} left in queue".format(num, self.message_queue.qsize()))
                except Exception:
                    logging.exception("Modem inoperational or another error occurred")
                finally:
                    if modem_locked:
                        self.close()

                        try:
                            self.modem_lock.release()
                        except RuntimeError:
                            logging.warning("Looks like the lock wasn't acquired, dealing with this...")

                logging.debug("{} thread waiting...".format(self.__class__.__name__))
                tm.sleep(self._modem_wait)

        def initialise_modem(self):
            """

            Opens the serial interface to the modem and performs the necessary registration
            checks for activity on the network. Raises an exception if we can't gather a
            suitable connection

            :return: None
            """
            if self._data is None:
                logging.info("Creating pyserial comms instance to modem")
                # Instantiation = opening of port hence why this is here and not in the constructor
                self._data = serial.Serial(
                    port=self.serial_port,
                    timeout=float(self.serial_timeout),
                    write_timeout=float(self.serial_timeout),
                    baudrate=self.serial_baud,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    # TODO: Extend to allow config file for HW flow control
                    rtscts=self.virtual,
                    dsrdtr=self.virtual
                )
            else:
                if not self._data.is_open:
                    logging.info("Opening existing modem serial connection")
                    self._data.open()
                ## TODO: Shared object now between threads, at startup, don't think this needs to be present
                else:
                    logging.warning("Modem appears to already be open, wasn't previously closed!?!")
#                    raise ModemConnectionException(
#                        "Modem appears to already be open, wasn't previously closed!?!")

            self._send_receive_messages("AT")
            self._send_receive_messages("ATE0\n")
            self._send_receive_messages("AT+SBDC")
            self._send_receive_messages("AT+SBDMTA=0")

            if not self.rockblock:
                reg_checks = 0
                registered = False

                while reg_checks < self.max_reg_checks:
                    logging.info("Checking registration on Iridium: attempt {} of {}".format(reg_checks, self.max_reg_checks))
                    registration = self._send_receive_messages("AT+CREG?")
                    check = True

                    if registration.splitlines()[-1] != "OK":
                        logging.warning("There's an issue with the registration response, won't parse: {}".
                                        format(registration))
                        check = False

                    if check:
                        (reg_type, reg_stat) = self._re_creg_response.search(registration).groups()
                        if int(reg_stat) not in [1, 5]:
                            logging.info("Not currently registered on network: status {}".format(int(reg_stat)))
                        else:
                            logging.info("Registered with status {}".format(int(reg_stat)))
                            registered = True
                            break
                    logging.debug("Waiting for registration")
                    tm.sleep(self.reg_check_interval)
                    reg_checks += 1

                if not registered:
                    raise ModemConnectionException("Failed to register on network")

        def _process_outstanding_messages(self):
            """
            Process the remains of the queue in the order SBD MO, file transfers

            We undertake the SBD first, as they're quicker and usually going to be used for key data. The SBD method
            will also check the MT SBD queue with Iridium which will pull down last, so we know all data is out before
            somebody messes with the configuration remotely

            :return: Number of messages processed
            """
            num = 0
            logging.debug("Processing currently queued messages...")
            while not self.message_queue.empty():
                msg = self.message_queue.get(timeout=1)
                try:
                    if msg[0] == self._priority_sbd_mo:
                        self.process_sbd_message(msg[1])
                    elif msg[0] == self._priority_file_mo:
                        # TODO: We need to batch file transfers together into
                        #  a single long running call
                        self._process_file_message(msg[1])
                    else:
                        raise ModemConnectionException("Invalid message type submitted {}".format(msg[0]))
                except ModemConnectionException:
                    # TODO: We need to put this back at the start of the queue, not the end...
                    logging.warning("Failed message handling, putting back to the queue...")
                    self.message_queue.put(msg)
                    raise

            while self._mt_queued:
                logging.info("Outstanding MT messages, collecting...")
                self.process_sbd_message()

        def _process_file_message(self, filename):
            """ Take a file and process it across the link via XMODEM

            TODO: This and all modem integration should be extrapolated to it's own library """
            
            def _callback(total_packets, success_count, error_count):
                logging.debug("{} packets, {} success, {} errors".format(total_packets, success_count, error_count))
                logging.debug("CD STATE: {}".format(self._data.cd))

                if error_count > self._dataxfer_errors:
                    logging.warning("Increase in error count")
                    self._dataxfer_errors = error_count
                # TODO: NAKs and error recall thresholds need to be configurable
                # if error_count > 0 and error_count % 3 == 0:
                #     logging.info("Third error response, re-establishing
                #     uplink")
                    try:
                        self._end_data_call()
                    except ModemConnectionException as e:
                        logging.warning("Unable to cleanly kill the call, will attempt a startup anyway: {}".format(e))
                    finally:
                        # If this doesn't work, we're likely down and might as
                        # well have the whole process restart again
                        self._start_data_call()

            def _getc(size, timeout=self._data.timeout):
                self._data.timeout = timeout
                read = self._data.read(size=size) or None
                logging.debug("_getc read {} bytes from data line".format(
                    len(read)
                ))
                return read

            def _putc(data, timeout=self._data.write_timeout):
                self._data.write_timeout = timeout
                logging.debug("_putc wrote {} bytes to data line".format(
                    len(data)
                ))
                size = self._data.write(data=data)
                return size

            # TODO: Catch errors and hangup the call!
            # TODO: Call thread needs to be separate to maintain uplink
            if self._start_data_call():
                # FIXME 2021: Try without preamble, make this optional
                self._send_filename(filename)

                xfer = xmodem.XMODEM(_getc, _putc)

                stream = open(filename, 'rb')
                xfer.send(stream, callback=_callback)
                logging.debug("Finished transfer")
                self._end_data_call()

                return True
            return False

        def _send_filename(self, filename):
            buffer = bytearray()
            res = None

            while not res or res.splitlines()[-1] != "A":
                res = self._send_receive_messages("@")

            res = self._send_receive_messages("FILENAME")
            # TODO: abstract the responses from being always a split and subscript
            if res.splitlines()[-1] != "GOFORIT":
                raise ModemConnectionException("Required response for FILENAME command not received")

            # We can only have two byte lengths, and we don't escape the two
            # markers characters since we're using the length marker with
            # otherwise fixed fields. We just use 0x1b as validation of the
            # last byte of the message
            bfile = os.path.basename(filename).encode("latin-1")[:255]
            file_length = os.stat(filename)[stat.ST_SIZE]
            length = len(bfile)
            buffer += struct.pack("BB", 0x1a, length)
            buffer += struct.pack("{}s".format(length), bfile)
            buffer += struct.pack("i", file_length)
            buffer += struct.pack("i", 1)
            buffer += struct.pack("i", 1)
            buffer += struct.pack("iB",
                                  binascii.crc32(bfile) & 0xffff,
                                  0x1b)

            res = self._send_receive_messages(buffer, raw=True)
            if res.splitlines()[-1] != "NAMERECV":
                raise ModemConnectionException("Could not transfer filename first: {}".format(res))

        def _start_data_call(self):
            if not self.dialup_number:
                logging.warning("No dialup number configured, will drop this message")
                return False

            response = self._send_receive_messages(
                "ATDT{}".format(self.dialup_number),
                timeout_override=self._call_timeout,
            )
            if not response.splitlines()[-1].startswith("CONNECT "):
                raise ModemConnectionException("Error opening call: {}".format(response))
            return True

        # TODO: Too much sleeping, use state based logic
        def _end_data_call(self):
            logging.debug("Two second sleep")
            tm.sleep(2)
            logging.debug("Two second sleep complete")
            response = self._send_receive_messages("+++".encode(), raw=True)
            logging.debug("One second sleep")
            tm.sleep(1)
            logging.debug("One second sleep complete")

            if response.splitlines()[-1] != "OK":
                raise ModemConnectionException("Did not switch to command mode to end call")

            response = self._send_receive_messages("ATH0")

            if response.splitlines()[-1] != "OK":
                raise ModemConnectionException("Did not hang up the call")
            else:
                logging.debug("Sleeping another second to wait for the line")
                tm.sleep(1)

        # TODO: Needs to impose a modem specific limit in length! 340 for 9603 (rockblock) and 1920 for 9522B
        # TODO: All this logic needs a rewrite, it's too dependent on MO message initiation
        def process_sbd_message(self, msg=None):
            if msg:
                text = msg.get_message_text()# .replace("\n", " ")

                response = self._send_receive_messages("AT+SBDWB={}".format(len(text)))
                if response.splitlines()[-1] != "READY":
                    raise ModemConnectionException("Error preparing for binary message: {}".format(response))

                payload = text.encode() if not msg.binary else text
                payload += ModemConnection.calculate_sbd_checksum(payload)
                response = self._send_receive_messages(payload, raw=True)

                if response.splitlines()[-2] != "0" \
                        and response.splitlines()[-1] != "OK":
                    raise ModemConnectionException("Error writing output binary for SBD".format(response))

            mo_status, mo_msn, mt_status, mt_msn, mt_len, mt_queued = None, 0, None, None, 0, 0
            self._mt_queued = False

            # TODO: BEGIN: this block with repeated SBDIX can overwrite the receiving message buffers
            while not mo_status or int(mo_status) > 4:
                response = self._send_receive_messages("AT+SBDIX", timeout_override=self.sbd_xfer_timeout)
                if response.splitlines()[-1] != "OK":
                    raise ModemConnectionException("Error submitting message: {}".format(response))

                mo_status, mo_msn, mt_status, mt_msn, mt_len, mt_queued = \
                    self._re_sbdix_response.search(response).groups()

            if int(mt_queued) > 0:
                logging.debug("We have messages still waiting at the GSS, will pick them up at end of message run")
                self._mt_queued = True

            # NOTE: Configure modems to not have ring alerts on SBD
            if int(mt_status) == 1:
                mt_message = self._send_receive_messages("AT+SBDRB", dont_decode=True)

                if mt_message:
                    try:
                        mt_message = mt_message[0:int(mt_len)+4]
                        length = mt_message[0:2]
                        message = mt_message[2:-2]
                        chksum = mt_message[-2:]
                    except IndexError:
                        raise ModemConnectionException(
                            "Message indexing was not successful for message ID {} length {}".format(
                                mt_msn, mt_len))
                    else:
                        calcd_chksum = sum(message) & 0xFFFF

                        try:
                            length = struct.unpack(">H", length)[0]
                            chksum = struct.unpack(">H", chksum)[0]
                        except (struct.error, IndexError) as e:
                            raise ModemConnectionException(
                                "Could not decompose the values from the incoming SBD message: {}".format(e.message))

                        if length != len(message):
                            logging.warning("Message length indicated {} is not the same as actual message: {}".format(
                                length, len(message)
                            ))
                        elif chksum != calcd_chksum:
                            logging.warning("Message checksum {} is not the same as calculated checksum: {}".format(
                                chksum, calcd_chksum
                            ))
                        else:
                            msg_dt = datetime.utcnow().strftime("%d%m%Y%H%M%S")
                            msg_filename = os.path.join(self.mt_destination, "{}_{}.msg".format(
                                mt_msn, msg_dt))
                            logging.info("Received MT message, outputting to {}".format(msg_filename))

                            try:
                                with open(msg_filename, "wb") as fh:
                                    fh.write(message)
                            except (OSError, IOError):
                                logging.error("Could not write {}, abandoning...".format(message))

            # TODO: END: this block with repeated SBDIX can overwrite the receiving message buffers

            response = self._send_receive_messages("AT+SBDD2")
            if response.splitlines()[-1] == "OK":
                logging.debug("Message buffers cleared")

            if int(mo_status) > 4:
                logging.warning("Adding message back into queue due to persistent MO status {}".format(mo_status))
                self.send_sbd(msg, 5)

                raise ModemConnectionException(
                    "Failed to send message with MO Status: {}, breaking...".format(mo_status))
            return True

        def _send_receive_messages(self, message, raw=False, dont_decode=False, timeout_override=None):
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
            if self._data is None or not self._data.is_open:
                raise ModemConnectionException('Cannot send message; data port is not open')
            self._data.flushInput()
            self._data.flushOutput()

            if not raw:
                self._data.write("{}{}".format(message.strip(), self._lineend).encode("latin-1"))
                logging.info('Message sent: "{}"'.format(message.strip()))
            else:
                self._data.write(message)
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
                reply += self._data.read_all()
                bytes_read += len(reply)

                duration = (datetime.utcnow() - start).total_seconds()
                if not len(reply):
                    if duration > msg_timeout:
                        logging.warning("We've read 0 bytes continuously for {} seconds, abandoning reads...".format(
                            duration
                        ))
                        # It's up to the caller to handle this scenario, just give back what's available...
                        raise ModemConnectionException("Response timeout from serial line...")
                    else:
                        #logging.debug("Waiting for response...")
                        tm.sleep(self.msg_wait_period)
                        continue

                start = datetime.utcnow()
                if not dont_decode:
                    logging.debug("Reply received: '{}'".format(reply.decode().strip()))

                cmd_match = self._re_modem_resp.search(reply.strip())
                if cmd_match:
                    tm.sleep(0.1)
                    if not self._data.in_waiting:
                        modem_response = True

            if dont_decode:
                logging.info("Response of {} bytes received".format(bytes_read))
            else:
                reply = reply.decode().strip()
                logging.info('Response received: "{}"'.format(reply))

            return reply

        def signal_check(self, min_signal=3):
            """
            Issue commands to the modem to evaluate the signal strength currently available

            :param min_signal: The minimum allowed signal for a positive result
            :return: boolean: True if signal checks OK, False otherwise
            """

            # Check we have a good enough signal to work with (>3)
            signal_test = self._send_receive_messages("AT+CSQ?")
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

            if type(signal_level) == int and signal_level >= min_signal:
                return True
            return False

        def send_sbd(self, message, timeout=None):
            self.message_queue.put((self._priority_sbd_mo, message))

        def send_file(self, filename, timeout=None):
            self.message_queue.put((self._priority_file_mo, filename))

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
            ModemConnection.instance = ModemConnection.__ModemConnection()
        else:
            logging.debug("ModemConnection already instantiated")

    def __getattr__(self, item):
        return getattr(self.instance, item)

    @staticmethod
    def calculate_sbd_checksum(payload):
        chk = bytearray()
        s = sum(payload)
        chk.append((s & 0xFF00) >> 8)
        chk.append(s & 0xFF)
        return chk


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
        self.modem.send_sbd(SBDMessage(message,
                                       binary=self.binary,
                                       include_date=include_date))
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
