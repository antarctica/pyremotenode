from . import BaseComms, Modem

__all__ = [
    "RudicsComms",
    "SbdComms",
    "SendSbdMessage",
]


class IridiumComms(BaseComms):
    def __init__(self, *args, **kwargs):
        BaseComms.__init__(self, *args, **kwargs)
        modem_args = self._scheduler.get_config('modem')
        self._modem = Modem(**modem_args)


class RudicsComms(IridiumComms):
    def __init__(self, *args, **kwargs):
        IridiumComms.__init__(self, *args, **kwargs)


class SbdComms(IridiumComms):
    def __init__(self, *args, **kwargs):
        IridiumComms.__init__(self, *args, **kwargs)


class SendSbdMessage(object):
    pass