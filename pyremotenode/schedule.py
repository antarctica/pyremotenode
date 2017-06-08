import logging
import os
import signal
import time

from .utils.system import pid_file

PID_FILE=os.path.join(os.sep, "tmp", "{0}.pid".format(__name__))


class MasterSchedule(object):
    """
        Master scheduler, MUST be run via the main thread
        Doesn't necessarily needs to be a singleton though, just only one starts at a time...
    """
    def __init__(self, configuration):
        logging.debug("Creating scheduler")
        self._cfg = configuration

        self._running = False
        self.init()

    def init(self):
        self._check_thread()
        self._configure_signals()

    def run(self):
        try:
            with pid_file(PID_FILE):
                self._running = True

                while self._running:
                    logging.info("Scheduler run")
                    time.sleep(10)
        finally:
            if os.path.exists(PID_FILE):
                os.unlink(PID_FILE)

    def start(self):
        logging.info("Starting scheduler")

        self.run()

    def stop(self):
        self._running = False

    ################################

    def _check_thread(self):
        """
            TODO: Checks scheduler is running in the main execution thread
        """
        pass

    def _configure_signals(self):
        signal.signal(signal.SIGTERM, self._sig_handler)
        signal.signal(signal.SIGINT, self._sig_handler)

    def _sig_handler(self, sig, stack):
        logging.debug("Signal handling {0} at frame {1}".format(sig, stack.f_code))
        self.stop()
