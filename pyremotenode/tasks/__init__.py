import logging
import sys
import traceback


class TaskException(Exception):
    pass


class BaseTask(object):
    OK = 0
    WARNING = 1
    CRITICAL = 2
    INVALID = -1

    def __init__(self, id, scheduler=None, **kwargs):
        self._sched = scheduler
        self._id = id
        self._state = None

    def __call__(self, action=None, **kwargs):
        if not action:
            action = 'default_action'

        if hasattr(self, action):
            logging.debug("Calling action {} on {}".format(action, self.__class__.__name__))
            ret_val = BaseTask.INVALID

            # TODO: What are we expecting? We should rethrow task based exceptions back to here
            try:
                ret_val = getattr(self, action)(**kwargs)
            except Exception:
                logging.error("Unhandled exception from within action {}".format(self.id))
                logging.error(traceback.format_exception())

            if self.sched:
                if ret_val == self.OK:
                    self._sched.add_ok(self.id)
                elif ret_val == self.WARNING:
                    self._sched.add_warning(self.id)
                elif ret_val == self.CRITICAL:
                    self._sched.add_critical(self.id)
                elif ret_val == self.INVALID:
                    self._sched.add_invalid(self.id)

            return ret_val
        else:
            raise TaskException("There is no {} action for the task {}!".format(action, self.__class__.__name__))

    def default_action(self, **kwargs):
        raise TaskException("There is no default exception defined for {}".format(self.__name__))

    # TODO: I don't really like this, it mixes messaging and state flags - change sensibly
    @property
    def state(self):
        try:
            int(self._state)
        except ValueError:
            return self._state
        return [s for s in ["OK", "WARNING", "CRITICAL", "INVALID"]
                if getattr(self, s) == self._state]

    @state.setter
    def state(self, state):
        self._state = state

    # NOTE: Dealing with singletons and properties, weirdness!?!
    @property
    def id(self):
        return self._id

    @property
    def sched(self):
        return self._sched


from pyremotenode.tasks.iridium import RudicsConnection, SBDSender
from pyremotenode.tasks.ssh import SshTunnel
from pyremotenode.tasks.ts7400 import Sleep
from pyremotenode.tasks.utils import Command

__all__ = ["Command", "Sleep", "RudicsConnection", "SBDSender", "SshTunnel"]