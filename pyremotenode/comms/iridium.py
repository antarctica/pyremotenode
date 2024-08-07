import binascii
import logging
import os
import re
import stat
import struct
import time as tm
from datetime import datetime, timedelta

import serial
import xmodem

from pyremotenode.comms.connections import BaseConnection, ConnectionException


class RudicsConnection(BaseConnection):
    re_sbdix_response = re.compile(r'^\+SBDIX:\s*(\d+), (\d+), (\d+), (\d+), (\d+), (\d+)', re.MULTILINE)
    re_creg_response = re.compile(r'^\+CREG:\s*(\d+),\s*(\d+),?.*', re.MULTILINE)
    re_msstm_response = re.compile(r'^-MSSTM: ([0-9a-f]{8}).*', re.MULTILINE | re.IGNORECASE)

    def __init__(self, cfg, *args, **kwargs):
        super().__init__(cfg, *args, **kwargs)

        # TODO: there should be some accessors for these as properties
        # Defeats https://github.com/pyserial/pyserial/issues/59 with socat usage
        self.virtual = bool(cfg['ModemConnection']['virtual']) \
            if 'virtual' in cfg['ModemConnection'] else False
        # Allows adaptation to Rockblocks reduced AT command set and non-Hayes line endings
        self._rockblock = bool(cfg['ModemConnection']['rockblock']) \
            if 'rockblock' in cfg['ModemConnection'] else False

        # MO dial up vars
        self.dialup_number = cfg['ModemConnection']['dialup_number'] \
            if 'dialup_number' in cfg['ModemConnection'] else None
        self._call_timeout = cfg['ModemConnection']['call_timeout'] \
            if "call_timeout" in cfg['ModemConnection'] else 120

        self.terminator = "\r"
        if self.virtual or self.rockblock:
            self.terminator = "\n"

        logging.info("Ready to connect to modem on {}".format(self.serial_port))

    def get_system_time(self):
        with self.thread_lock:
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
                    result = self.modem_command("AT-MSSTM")
                    if result.splitlines()[-1] != "OK":
                        raise ConnectionException("Error code response from modem, cannot continue")

                    result = self.re_msstm_response.match(result).group(1)

                    now = timedelta(seconds=int(result, 16) / (1. / 0.09))
                else:
                    return None
            except (ConnectionException, serial.SerialException, serial.SerialTimeoutException):
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

    def initialise_modem(self):
        """

        Opens the serial interface to the modem and performs the necessary registration
        checks for activity on the network. Raises an exception if we can't gather a
        suitable connection

        :return: None
        """
        if self.data_conn is None:
            if os.path.exists(self.serial_port):
                logging.info("Creating pyserial comms instance to modem: {}".format(self.serial_port))
                # Instantiation = opening of port hence why this is here and not in the constructor
                self.data_conn = serial.Serial(
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
                raise ConnectionException("There is no path: {} so cannot attempt to open it as a serial connection".
                                          format(self.serial_port))

        else:
            if not self.data_conn.is_open:
                logging.info("Opening existing modem serial connection")
                self.data_conn.open()
            ## TODO: Shared object now between threads, at startup, don't think this needs to be present
            else:
                logging.warning("Modem appears to already be open, wasn't previously closed!?!")
#                    raise ConnectionException(
#                        "Modem appears to already be open, wasn't previously closed!?!")

        self.modem_command("AT")
        self.modem_command("ATE0\n")
        self.modem_command("AT+SBDC")

        if not self.rockblock:
            reg_checks = 0
            registered = False

            while reg_checks < self.max_reg_checks:
                logging.info("Checking registration on Iridium: attempt {} of {}".format(reg_checks, self.max_reg_checks))
                registration = self.modem_command("AT+CREG?")
                check = True

                if registration.splitlines()[-1] != "OK":
                    logging.warning("There's an issue with the registration response, won't parse: {}".
                                    format(registration))
                    check = False

                if check:
                    (reg_type, reg_stat) = self.re_creg_response.search(registration).groups()
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
                raise ConnectionException("Failed to register on network")

    # TODO: All this logic needs a rewrite, it's too dependent on MO message initiation
    def process_message(self, msg=None):
        if msg:
            text = msg.get_message_text()# .replace("\n", " ")

            response = self.modem_command("AT+SBDWB={}".format(len(text)))
            if response.splitlines()[-1] != "READY":
                raise ConnectionException("Error preparing for binary message: {}".format(response))

            payload = text.encode() if not msg.binary else text
            payload += RudicsConnection.calculate_sbd_checksum(payload)
            response = self.modem_command(payload, raw=True)

            if response.splitlines()[-2] != "0" \
                and response.splitlines()[-1] != "OK":
                raise ConnectionException("Error writing output binary for SBD".format(response))

        mo_status, mo_msn, mt_status, mt_msn, mt_len, mt_queued = None, 0, None, None, 0, 0
        self.mt_queued = False

        # TODO: BEGIN: this block with repeated SBDIX can overwrite the receiving message buffers
        while not mo_status or int(mo_status) > 4:
            response = self.modem_command("AT+SBDIX", timeout_override=self.msg_xfer_timeout)
            if response.splitlines()[-1] != "OK":
                raise ConnectionException("Error submitting message: {}".format(response))

            mo_status, mo_msn, mt_status, mt_msn, mt_len, mt_queued = \
                self.re_sbdix_response.search(response).groups()

        if int(mt_queued) > 0:
            logging.debug("We have messages still waiting at the GSS, will pick them up at end of message run")
            self.mt_queued = True

        # NOTE: Configure modems to not have ring alerts on SBD
        if int(mt_status) == 1:
            mt_message = self.modem_command("AT+SBDRB", dont_decode=True)

            if mt_message:
                try:
                    mt_message = mt_message[0:int(mt_len)+4]
                    length = mt_message[0:2]
                    message = mt_message[2:-2]
                    chksum = mt_message[-2:]
                except IndexError:
                    raise ConnectionException(
                        "Message indexing was not successful for message ID {} length {}".format(
                            mt_msn, mt_len))
                else:
                    calcd_chksum = sum(message) & 0xFFFF

                    try:
                        length = struct.unpack(">H", length)[0]
                        chksum = struct.unpack(">H", chksum)[0]
                    except (struct.error, IndexError) as e:
                        raise ConnectionException(
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

        response = self.modem_command("AT+SBDD2")
        if response.splitlines()[-1] == "OK":
            logging.debug("Message buffers cleared")

        if int(mo_status) > 4:
            logging.warning("Adding message back into queue due to persistent MO status {}".format(mo_status))
            self.send_message(msg, 5)

            raise ConnectionException(
                "Failed to send message with MO Status: {}, breaking...".format(mo_status))
        return True

    def process_transfer(self, filename):
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
                except ConnectionException as e:
                    logging.warning("Unable to cleanly kill the call, will attempt a startup anyway: {}".format(e))
                finally:
                    # If this doesn't work, we're likely down and might as
                    # well have the whole process restart again
                    self._start_data_call()

        def _getc(size, timeout=self.data_conn.timeout):
            self.data_conn.timeout = timeout
            read = self.data_conn.read(size=size) or None
            logging.debug("_getc read {} bytes from data line".format(
                len(read)
            ))
            return read

        def _putc(data, timeout=self.data_conn.write_timeout):
            """

            Args:
                data:
                timeout:

            Returns:

            """
            self.data_conn.write_timeout = timeout
            logging.debug("_putc wrote {} bytes to data line".format(
                len(data)
            ))
            size = self.data_conn.write(data=data)
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
            res = self.modem_command("@")

        res = self.modem_command("FILENAME")
        # TODO: abstract the responses from being always a split and subscript
        if res.splitlines()[-1] != "GOFORIT":
            raise ConnectionException("Required response for FILENAME command not received")

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

        res = self.modem_command(buffer, raw=True)
        if res.splitlines()[-1] != "NAMERECV":
            raise ConnectionException("Could not transfer filename first: {}".format(res))

    def _start_data_call(self):
        if not self.dialup_number:
            logging.warning("No dialup number configured, will drop this message")
            return False

        response = self.modem_command(
            "ATDT{}".format(self.dialup_number),
            timeout_override=self._call_timeout,
        )
        if not response.splitlines()[-1].startswith("CONNECT "):
            raise ConnectionException("Error opening call: {}".format(response))
        return True

    # TODO: Too much sleeping, use state based logic
    def _end_data_call(self):
        logging.debug("Two second sleep")
        tm.sleep(2)
        logging.debug("Two second sleep complete")
        response = self.modem_command("+++".encode(), raw=True)
        logging.debug("One second sleep")
        tm.sleep(1)
        logging.debug("One second sleep complete")

        if response.splitlines()[-1] != "OK":
            raise ConnectionException("Did not switch to command mode to end call")

        response = self.modem_command("ATH0")

        if response.splitlines()[-1] != "OK":
            raise ConnectionException("Did not hang up the call")
        else:
            logging.debug("Sleeping another second to wait for the line")
            tm.sleep(1)

    @property
    def rockblock(self):
        return self._rockblock

    @staticmethod
    def calculate_sbd_checksum(payload):
        chk = bytearray()
        s = sum(payload)
        chk.append((s & 0xFF00) >> 8)
        chk.append(s & 0xFF)
        return chk


class CertusConnection(BaseConnection):
    def get_system_time(self):
        pass

    def process_message(self, msg=None):
        pass

    def process_outstanding_messages(self):
        pass

    def process_transfer(self, filename):
        pass

    def send_file(self):
        pass

    def send_message(self):
        pass

