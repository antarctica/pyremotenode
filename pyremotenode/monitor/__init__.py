import threading


class BaseMonitor(object):
    def healthy(self):
        raise NotImplementedError


class MonitorSBC(BaseMonitor):
    def __init__(self,
                 modem):
        self._modem = modem

    def healthy(self):
        return self.modem_check() \
            and self.temperature_check() \
            and self.voltage_check()

    def modem_check(self):
        raise NotImplementedError

    def temperature_check(self):
        raise NotImplementedError

    def voltage_check(self):
        raise NotImplementedError
