from . import BaseComms

__all__ = [
    "SshTunnel",
]


class SshTunnel(BaseComms):
    def __init__(self, *args, **kwargs):
        BaseComms.__init__(self, *args, **kwargs)