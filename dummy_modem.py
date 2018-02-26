import argparse
import logging
import os
import re
import sys
import time

from subprocess import Popen, PIPE, STDOUT
from threading import Thread

from pyremotenode.utils import setup_logging

log = setup_logging(__name__)
logging.info("DummyModem")

class DummyModem(object):
    PREFIX = "AT"
    REPORT_ON = "+CIER=1,1"
    REPORT_OFF = "+CIER=0,0"
    CONFIGURE_CALL = "AT+CBST=71,0,1"
    START_CALL = "ATDT0088160000660"
    HANGUP_CALL = "ATH0"

    def __init__(self, name="dummy", directory=os.path.join(os.sep, "tmp")):
        self._location = os.path.join(directory, name)
        self._thread = None

    def get_thread(self):
        return self._thread

    def start(self):
        logging.info("Starting dummy modem at {}".format(self._location))
        self._socat = Popen(["socat", "pty,echo=0,link={},raw,nonblock".format(self._location), "-"],
                            stdin=PIPE, stdout=PIPE)

        if self._socat.poll() is not None:
            raise SocatException(self._socat.communicate(timeout=10))

        self._thread = Thread(target=self.run)
        self._thread.start()

    def run(self):
        stdin = self._socat.stdin
        sbd_mo_count = 1
        sbd_mt_count = 1
        sbd_mt_msg = "UPDATE: http://circadiansystems.co.uk/test.cfg"

        with self._socat:
            registration_checks = 0

            for stdout in self._socat.stdout:
                response = None
                reply_lines = ["", "OK"]
                command = stdout.decode().strip()

                logging.debug("Received: {}".format(command))

                # TODO: Change this to be a control sequence try block, it'll be nicer
                #while response != "END":
                #    response = input("Response: ").strip()
                #    if response != "END":
                #        reply_lines.append(response)

                if command.startswith("AT+CREG?"):
                    registered = 4
                    if registration_checks == 2:
                        logging.debug("Registered")
                        registered = 1

                    time.sleep(1)
                    reply_lines = ["+CREG:000,00{}".format(registered), "", "OK"]
                    registration_checks += 1
                elif command.startswith("AT+CSQ"):
                    logging.debug("Signal request")
                    time.sleep(1)
                    reply_lines = ["+CSQ:5", "", "OK"]
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

                if len(reply_lines):
                    response = "\r\n".join(reply_lines)
                    logging.debug("Sending {}".format(response.replace("\r", "\n")))
                    stdin.write((response+"\r\n").encode('latin-1'))
                    stdin.flush()
                else:
                    logging.info("You've input an invalid response")

    def stop(self):
        self._socat.terminate()


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

    log.info("Gracefully stopped using the dummy modem!")
