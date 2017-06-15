from . import BaseComms, Modem

__all__ = [
    "RudicsComms",
    "SbdComms",
    "SendSbdMessage",
]


class IridiumComms(BaseComms):
    def __init__(self, *args, **kwargs):
        BaseComms.__init__(self, *args, **kwargs)


class RudicsComms(IridiumComms):
    def __init__(self, *args, **kwargs):
        IridiumComms.__init__(self, *args, **kwargs)


class SbdComms(IridiumComms):
    def __init__(self, *args, **kwargs):
        IridiumComms.__init__(self, *args, **kwargs)


class SendSbdMessage(object):
    pass