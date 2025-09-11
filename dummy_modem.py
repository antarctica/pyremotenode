import argparse
import logging
import os
import re
import time

from subprocess import Popen, PIPE
from threading import Thread

from pyremotenode.utils import setup_logging

log = setup_logging(__name__)
logging.getLogger(__name__).setLevel(logging.DEBUG)
logging.info("DummyModem")


class DummyModem(object):
    PREFIX = "AT"
    REPORT_ON = "+CIER=1,1"
    REPORT_OFF = "+CIER=0,0"
    CONFIGURE_CALL = "AT+CBST=71,0,1"
    START_CALL = "ATDT00881600005452"
    RESPOND_CALL = "CONNECT 115200"
    ESCAPE = "+++"
    HANGUP_CALL = "ATH0"

    def __init__(self, name="dummy", directory=os.path.join(os.sep, "tmp")):
        self._location = os.path.join(directory, name)
        self._thread = None
        self._rudics = RudicsMODummyConnection(self.write)
        self._socat = None

    def get_thread(self):
        return self._thread

    def start(self):
        logging.info("Starting dummy modem at {}".format(self._location))
        self._socat = Popen(["socat", "pty,link={},raw,opost=0".format(self._location), "-"],
                            stdin=PIPE, stdout=PIPE, universal_newlines=False)

        if self._socat.poll() is not None:
            raise SocatException(self._socat.communicate(timeout=10))

        self._thread = Thread(target=self.run)
        self._thread.start()

    def write(self, data):
        self._socat.stdin.write(data)
        self._socat.stdin.flush()

    def run(self):
        stdin = self._socat.stdin
        sbd_mo_count = 1
        sbd_mt_count = 1
        sbd_mt_msg = "UPDATE: http://circadiansystems.co.uk/test.cfg"

        with self._socat:
            registration_checks = 0
            in_data_mode = False

            # TODO: If we're going to use this going forward, extrapolate this nasty
            # long if clause to state machines for the various mechanisms
            for stdout in self._socat.stdout:
                response = None
                reply_lines = ["", "OK"]

                command = stdout

                if in_data_mode:
                    # TODO: Escape sequence to modem should happen with 1sec gap
                    # TODO: For acting as a true binary passthrough requires work....
                    if command.endswith(DummyModem.ESCAPE.encode("ascii")):
                        logging.debug("Received: {}".format(command))
                        in_data_mode = False
                    else:
                        logging.debug("Sending non modem data out")
                        self._rudics.write(stdout)
                        continue
                else:
                    command = command.decode().strip()
                    logging.debug("Received: {}".format(command))

                    ###
                    #   General comms
                    #
                    if command.startswith("AT+CREG?"):
                        registered = 4
                        if registration_checks == 0:
                            logging.debug("Registered")
                            registered = 1

                        time.sleep(1)
                        reply_lines = ["+CREG:000,00{}".format(registered), "", "OK"]
                        registration_checks += 1
                    elif command.startswith("AT+CSQ"):
                        logging.debug("Signal request")
                        time.sleep(1)
                        reply_lines = ["+CSQ:5", "", "OK"]
                    ###
                    #   RUDICS MO comms
                    #
                    elif command.startswith(DummyModem.START_CALL):
                        logging.debug("Starting a dial up data call")
                        time.sleep(0.5)
                        reply_lines = [DummyModem.RESPOND_CALL]

                        self._rudics.start()
                        in_data_mode = True
                    elif command.startswith(DummyModem.HANGUP_CALL):
                        reply_lines = ["OK"]
                    ###
                    #   SBD specific messages
                    #
                    elif command.startswith("AT+SBDWT="):
                        logging.debug("Got message: {}".format(command[9:]))
                    elif command.startswith("AT+SBDIX"):
                        logging.debug("Sending message with slight delay")
                        time.sleep(1)
                        sbd_mo_count += 1
                        reply_lines = ["+SBDIX:0, {}, 1, {}, {}, 0".format(sbd_mo_count, sbd_mt_count, len(sbd_mt_msg)), "", "OK"]
                    elif command.startswith("AT+SBDRT"):
                        logging.debug("Giving MT message")
                        sbd_mt_count += 1
                        reply_lines = ["+SBDRT:", sbd_mt_msg, "", "OK"]
                    elif command.startswith("AT+SBDD2"):
                        logging.debug("Cleared buffers")
                        reply_lines = ["OK"]

                ###
                #   Replying mechanism
                #
                if len(reply_lines):
                    response = "\n".join(reply_lines)
                    logging.debug("Sending {}".format(response))
                    stdin.write((response+"\n").encode("latin-1"))
                    stdin.flush()
                else:
                    logging.info("You've input an invalid response")

    def stop(self):
        self._socat.terminate()

    def _getc(self, size, timeout=1):
        return self._socat.stdout.read(size) or None

    def _putc(self, data, timeout=1):
        return self._socat.stdin.write(data)


class RudicsMODummyConnection(object):
    def __init__(self, write_func, location="127.0.0.1:33002"):
        self._location = location
        self._thread = Thread(target=self.run)
        self._socat = None
        self._wfunc = write_func

    def start(self):
        logging.info("Starting uplink connection to receiver at {}".format(self._location))
        self._socat = Popen(["socat", "-", "tcp4-connect:127.0.0.1:33002"],
                            stdin=PIPE, stdout=PIPE)

        if self._socat.poll() is not None:
            raise SocatException(self._socat.communicate(timeout=10))

        self._thread.start()

    def write(self, data):
        self._socat.stdin.write(data)
        self._socat.stdin.flush()

    def run(self):
        with self._socat:
            for stdout in self._socat.stdout:
                self._wfunc(stdout)


class SocatException(Exception):
    pass


if __name__ == '__main__':
    a = argparse.ArgumentParser()
    a.add_argument("directory", help="Directory in which to create it")
    a.add_argument("name", help="Name of device to create")
    args = vars(a.parse_args())

    dm = DummyModem(args['name'], args['directory'])
    dm.start()

    quit = False

    logging.info("At any time type 'quit' to finish the program...")

    while dm.get_thread().is_alive() and not quit:
        command = input()
        quit = True if re.match(r'quit', command) else False

        if quit: dm.stop()

    logging.info("Gracefully stopped using the dummy modem!")
