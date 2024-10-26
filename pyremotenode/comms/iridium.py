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
            # TODO: Shared object now between threads, at startup, don't think this needs to be present
            else:
                logging.warning("Modem appears to already be open, wasn't previously closed!?!")
#                    raise ConnectionException(
#                        "Modem appears to already be open, wasn't previously closed!?!")

        self.modem_command("AT")
        self.modem_command("ATE0\n")
        self.modem_command("AT+SBDC")
        self.modem_command("AT+SBDMTA=0")

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

    def poll_for_messages(self):
        logging.info("Outstanding MT messages, collecting...")
        self.process_message(None)

    # TODO: All this logic needs a rewrite, it's too dependent on MO message initiation
    def process_message(self, msg):
        if msg is not None:
            text = msg.get_message_text()

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

        # TODO: BEGIN: this block with repeated SBDIX can overwrite the receiving message buffers
        while not mo_status or int(mo_status) > 4:
            response = self.modem_command("AT+SBDIX", timeout_override=self.msg_xfer_timeout)
            if response.splitlines()[-1] != "OK":
                raise ConnectionException("Error submitting message: {}".format(response))

            mo_status, mo_msn, mt_status, mt_msn, mt_len, mt_queued = \
                self.re_sbdix_response.search(response).groups()

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
    CRC_TABLE = [
        0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50a5, 0x60c6, 0x70e7,
        0x8108, 0x9129, 0xa14a, 0xb16b, 0xc18c, 0xd1ad, 0xe1ce, 0xf1ef,
        0x1231, 0x0210, 0x3273, 0x2252, 0x52b5, 0x4294, 0x72f7, 0x62d6,
        0x9339, 0x8318, 0xb37b, 0xa35a, 0xd3bd, 0xc39c, 0xf3ff, 0xe3de,
        0x2462, 0x3443, 0x0420, 0x1401, 0x64e6, 0x74c7, 0x44a4, 0x5485,
        0xa56a, 0xb54b, 0x8528, 0x9509, 0xe5ee, 0xf5cf, 0xc5ac, 0xd58d,
        0x3653, 0x2672, 0x1611, 0x0630, 0x76d7, 0x66f6, 0x5695, 0x46b4,
        0xb75b, 0xa77a, 0x9719, 0x8738, 0xf7df, 0xe7fe, 0xd79d, 0xc7bc,
        0x48c4, 0x58e5, 0x6886, 0x78a7, 0x0840, 0x1861, 0x2802, 0x3823,
        0xc9cc, 0xd9ed, 0xe98e, 0xf9af, 0x8948, 0x9969, 0xa90a, 0xb92b,
        0x5af5, 0x4ad4, 0x7ab7, 0x6a96, 0x1a71, 0x0a50, 0x3a33, 0x2a12,
        0xdbfd, 0xcbdc, 0xfbbf, 0xeb9e, 0x9b79, 0x8b58, 0xbb3b, 0xab1a,
        0x6ca6, 0x7c87, 0x4ce4, 0x5cc5, 0x2c22, 0x3c03, 0x0c60, 0x1c41,
        0xedae, 0xfd8f, 0xcdec, 0xddcd, 0xad2a, 0xbd0b, 0x8d68, 0x9d49,
        0x7e97, 0x6eb6, 0x5ed5, 0x4ef4, 0x3e13, 0x2e32, 0x1e51, 0x0e70,
        0xff9f, 0xefbe, 0xdfdd, 0xcffc, 0xbf1b, 0xaf3a, 0x9f59, 0x8f78,
        0x9188, 0x81a9, 0xb1ca, 0xa1eb, 0xd10c, 0xc12d, 0xf14e, 0xe16f,
        0x1080, 0x00a1, 0x30c2, 0x20e3, 0x5004, 0x4025, 0x7046, 0x6067,
        0x83b9, 0x9398, 0xa3fb, 0xb3da, 0xc33d, 0xd31c, 0xe37f, 0xf35e,
        0x02b1, 0x1290, 0x22f3, 0x32d2, 0x4235, 0x5214, 0x6277, 0x7256,
        0xb5ea, 0xa5cb, 0x95a8, 0x8589, 0xf56e, 0xe54f, 0xd52c, 0xc50d,
        0x34e2, 0x24c3, 0x14a0, 0x0481, 0x7466, 0x6447, 0x5424, 0x4405,
        0xa7db, 0xb7fa, 0x8799, 0x97b8, 0xe75f, 0xf77e, 0xc71d, 0xd73c,
        0x26d3, 0x36f2, 0x0691, 0x16b0, 0x6657, 0x7676, 0x4615, 0x5634,
        0xd94c, 0xc96d, 0xf90e, 0xe92f, 0x99c8, 0x89e9, 0xb98a, 0xa9ab,
        0x5844, 0x4865, 0x7806, 0x6827, 0x18c0, 0x08e1, 0x3882, 0x28a3,
        0xcb7d, 0xdb5c, 0xeb3f, 0xfb1e, 0x8bf9, 0x9bd8, 0xabbb, 0xbb9a,
        0x4a75, 0x5a54, 0x6a37, 0x7a16, 0x0af1, 0x1ad0, 0x2ab3, 0x3a92,
        0xfd2e, 0xed0f, 0xdd6c, 0xcd4d, 0xbdaa, 0xad8b, 0x9de8, 0x8dc9,
        0x7c26, 0x6c07, 0x5c64, 0x4c45, 0x3ca2, 0x2c83, 0x1ce0, 0x0cc1,
        0xef1f, 0xff3e, 0xcf5d, 0xdf7c, 0xaf9b, 0xbfba, 0x8fd9, 0x9ff8,
        0x6e17, 0x7e36, 0x4e55, 0x5e74, 0x2e93, 0x3eb2, 0x0ed1, 0x1ef0,
    ]

    @staticmethod
    def calculate_crc16(payload, crc=0):
        """

        Lifted from https://github.com/tehmaze/xmodem/blob/74fad59be70b6ea173484f52f08bbcfc374b9026/xmodem/__init__.py#L663
        The GroundControl CRC algorithm is the equivalent of the XMODEM implementation so
        might as well use that

        Args:
            payload:
            crc:

        Returns:
            data:
        """
        for char in bytearray(payload):
            crctbl_idx = ((crc >> 8) ^ char) & 0xff
            crc = ((crc << 8) ^ CertusConnection.CRC_TABLE[crctbl_idx]) & 0xffff
        return crc & 0xffff

    def get_system_time(self):
        return None

    def initialise_modem(self):
        super().initialise_modem()

        devices = ['"Mini"']
        reply = self.modem_command("AT+CGMM")

        try:
            device = reply.split(self.terminator)[0].split(":")[1].strip()
        except (IndexError, ValueError):
            raise ConnectionException("Could not parse device response")

        if device not in devices:
            raise ConnectionException("{} can only be used with {}, but we got {}".
                                      format(self.__class__.__name__, " or ".join(devices), device))

        # TODO: https://docs.rockremote.io/serial-interface#status-of-mt-imt
        #  this will need to be run periodically as here we switch off unsolicited messages
        # TODO: handle unsolicited messages and avoid turning them off
        reply = self.modem_command("AT+UNS=0")

        if reply.split()[-1] != "OK":
            raise ConnectionException("Cannot switch Certus modem to solicited messaging mode")

    def poll_for_messages(self):
        response = self.modem_command("AT+IMTMTS")
        mt_queued = response.splitlines()[0].startswith("+IMTMTS: ")

        if mt_queued:
            msg_info = response.splitlines()[0].replace("+IMTMTS: ", "").strip()
            topic_id, mt_msg_id, mt_msg_len = msg_info.split(",")

            mt_message = self.modem_command("AT+IMTRB={}".format(topic_id), dont_decode=True)

            if mt_message:
                mt_message = mt_message.rstrip("\rOK\r".encode("ascii"))
                try:
                    message = mt_message[0:-2]
                    chksum = mt_message[-2:]
                except IndexError:
                    raise ConnectionException(
                        "Message indexing was not successful for message ID {} length {}".format(
                            mt_msg_id, mt_msg_len))
                else:
                    calcd_chksum = CertusConnection.calculate_crc16(message)

                    try:
                        recv_chksum = struct.unpack(">H", chksum)[0]
                    except (struct.error, IndexError) as e:
                        raise ConnectionException(
                            "Could not decompose the values from the incoming SBD message: {}".format(e.message))

                    self.output_recv_message(mt_msg_id,
                                             # Remove the unnecessary checksum inclusion
                                             int(mt_msg_len) - 2,
                                             message,
                                             calcd_chksum,
                                             recv_chksum)
                    response = self.modem_command("AT+IMTA={}".format(mt_msg_id))
                    if response.splitlines()[-1] == "OK":
                        logging.info("Acknowledged IMT message ID {}".format(mt_msg_id))

    def process_message(self, msg):
        if msg:
            text = msg.get_message_text()

            response = self.modem_command("AT+IMTWB={}".format(len(text)))
            if response.startswith("+IMTWB ERROR: 2"):
                logging.warning("Message is too big")
                return True
            elif not response.splitlines()[-1].strip().endswith("READY"):
                raise ConnectionException("Error preparing for binary message: {}".format(response))

            payload = text.encode() if not msg.binary else text
            payload += CertusConnection.calculate_crc16(payload).to_bytes(2, "big")
            response = self.modem_command(payload, raw=True)

            if response.splitlines()[-1] != "OK":
                raise ConnectionException("Error writing output binary for SBD".format(response))
            message_id = response.splitlines()[0].split(":")[1]
            logging.info("Sent {} bytes with message ID {}".format(len(payload), message_id))

        return True

    def process_transfer(self, filename):
        pass


