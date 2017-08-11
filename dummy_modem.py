import argparse
import logging
import os
import re
import sys

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
        self._socat = Popen([ "socat", "pty,link={}".format(self._location), "stdout" ],
                            stdin=PIPE, stdout=PIPE,
                            universal_newlines=True, bufsize=1)

        if self._socat.poll() is not None:
            raise SocatException(self._socat.communicate(timeout=10))

        self._thread = Thread(target=self.run)
        self._thread.run()

    def run(self):
        input = self._socat.stdin

        with self._socat:
            for output in self._socat.stdout:
                logging.debug(output)

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
    while dm.get_thread().is_alive() and not quit:
        command = input("Command: ")
        quit = True if re.match(r'quit', command) else False

    log.info("Gracefully stopped using the dummy modem!")
