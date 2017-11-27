import logging
import threading as t
import time as tm

from pyremotenode.tasks import BaseTask


class ModemConnection(object):
    class __ModemConnection:
        modem_lock = t.Lock()
        running = False

        def __init__(self):
            self._thread = t.Thread(name=self.__class__.__name__, target=self.run)

        def run(self):
            while self.running:
                logging.debug("{} thread running...".format(self.__class__.__name__))
                tm.sleep(2)

        @property
        def running(self):
            with self.modem_lock:
                return self.running

        @running.setter
        def set_running(self, state=True):
            with self.modem_lock:
                self.running = state

        @property
        def modem_lock(self):
            return self.modem_lock

    instance = None

    def __init__(self, *args, **kwargs):
        if not self.instance:
            self.instance = ModemConnection.__ModemConnection(*args, **kwargs)

    def __getattr__(self, item):
        return getattr(self.instance, item)


class RudicsConnection(BaseTask):
    class __RudicsConnection:
        def __init__(self, *args, **kwargs):
            BaseTask.__init__(self, *args, **kwargs)
            self.modem = ModemConnection()

        def start(self):
            logging.debug("Running start action for RudicsConnection")

            with self.modem.modem_lock:
                self.modem.set_running()

        def stop(self):
            logging.debug("Running stop action for RudicsConnection")

            with self.modem.modem_lock:
                self.modem.set_running(False)

    instance = None

    def __init__(self, *args, **kwargs):
        if not self.instance:
            self.instance = RudicsConnection.__RudicsConnection(*args, **kwargs)

    def __getattr__(self, item):
        return getattr(self.instance, item)


class SBDMessage(BaseTask):
    def __init__(self, *args, **kwargs):
        BaseTask.__init__(self, *args, **kwargs)
        self.modem = ModemConnection()

    def default_action(self, *args, **kwargs):
        logging.debug("Running default action for SBDMessage")

        with self.modem.modem_lock:
            # TODO: Queue messages against the modem
            self.modem.set_running()
            # TODO: Catch any errors and reset the modem
