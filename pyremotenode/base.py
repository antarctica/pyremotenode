class BaseItem(object):
    OK = 0
    WARNING = 1
    CRITICAL = 2
    INVALID = -1

    def __init__(self, scheduler, *args, **kwargs):
        self._scheduler = scheduler

    def action(self, name):
        raise NotImplementedError


class ScheduleConfigurationError(Exception):
    pass