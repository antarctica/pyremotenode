import logging

from pyremotenode.base import BaseItem


class BaseMonitor(BaseItem):
    def __init__(self, *args, **kwargs):
        BaseItem.__init__(self, *args, **kwargs)
        self.last_status = BaseItem.OK

    def action(self, name):
        logging.debug("Initiating item action {0}".format(name))
        self.last_status = self.monitor()

    def monitor(self):
        raise NotImplementedError


class MonitorSBC(BaseMonitor):
    def __init__(self,
                 modem, *args, **kwargs):
        BaseMonitor.__init__(self, *args, **kwargs)
        self._modem = modem

    def monitor(self):
        return self.modem_check() \
            and self.temperature_check() \
            and self.voltage_check()

    def modem_check(self):
        raise NotImplementedError

    def temperature_check(self):
        raise NotImplementedError

    def voltage_check(self):
        raise NotImplementedError


class MonitorConfigureError(Exception):
    pass


class MonitorCheckError(Exception):
    pass
