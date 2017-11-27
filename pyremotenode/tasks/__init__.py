import logging

class TaskException(Exception):
    pass


class BaseTask(object):
    OK = 0
    WARNING = 1
    CRITICAL = 2
    INVALID = -1

    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, action=None, *args, **kwargs):
        if not action:
            action = 'default_action'

        if hasattr(self, action):
            logging.debug("Calling action {} on {}".format(action, self.__class__.__name__))
            # TODO: Here we can deal with statuses!
            return getattr(self, action)(*args, **kwargs)
        else:
            raise TaskException("There is no {} action for the task {}!".format(action, self.__class__.__name__))

    def default_action(self, *args, **kwargs):
        raise TaskException("There is no default exception defined for {}".format(self.__name__))


from pyremotenode.tasks.iridium import RudicsConnection, SBDMessage
from pyremotenode.tasks.ssh import SshTunnel
from pyremotenode.tasks.ts7400 import Sleep
from pyremotenode.tasks.utils import Command

__all__ = ["Command", "Sleep", "RudicsConnection", "SBDMessage", "SshTunnel"]
