import logging


class MasterSchedule(object):
    def __init__(self, configuration):
        logging.debug("Creating scheduler")
        self._cfg = configuration

        self.init()

    def init(self):
        pass

    def start(self):
        logging.info("Starting scheduler")
