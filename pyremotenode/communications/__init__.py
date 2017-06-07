class BaseComms(object):
    def start(self):
        raise NotImplementedError

    def is_ready(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError


class ModemComms(BaseComms):
    pass
