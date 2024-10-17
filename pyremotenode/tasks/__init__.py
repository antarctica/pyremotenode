import logging
import sys
import time
import traceback


class TaskException(Exception):
    pass


class BaseTask(object):
    OK = 0
    WARNING = 1
    CRITICAL = 2
    INVALID = -1

    def __init__(self, id,
                 scheduler=None,
                 not_due=None,
                 binary=False,
                 **kwargs):
        self._sched = scheduler
        self._id = id
        self._state = None
        self._last_state = self._state
        self._binary = binary
        # TODO: Currently not used yet
        self._ready = True

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
                logging.error("Unhandled exception from within action {}".format(self._id))
                logging.error(traceback.format_exc())

            # TODO: Fix misappropriation of state in implementations and use it with last_state to avoid flapping
            if self._sched:
                if ret_val == self.OK:
                    self._sched.add_ok(self._id)
                elif ret_val == self.WARNING:
                    self._sched.add_warning(self._id)
                elif ret_val == self.CRITICAL:
                    self._sched.add_critical(self._id)
                elif ret_val == self.INVALID:
                    self._sched.add_invalid(self._id)

            return ret_val
        else:
            raise TaskException("There is no {} action for the task {}!".format(action, self.__class__.__name__))

    def default_action(self, **kwargs):
        raise TaskException("There is no default action defined for {}".format(self.__name__))

    @property
    def binary(self):
        return self._binary

    # TODO: I don't really like this, it mixes messaging and state flags - change sensibly
    @property
    def state(self):
        try:
            int(self._state)
        except TypeError:
            return self._state
        return [s for s in ["OK", "WARNING", "CRITICAL", "INVALID"]
                if getattr(self, s) == self._state]

    @state.setter
    def state(self, state):
        self._state = state

    @property
    def ready(self):
        return self._ready

    @ready.setter
    def ready(self, ready):
        self._ready = ready


from pyremotenode.tasks.iridium import ModemConnection, FileSender, SBDSender, WakeupTask, MTMessageCheck
from pyremotenode.tasks.loh import SendLoHBaselines
from pyremotenode.tasks.ssh import SshTunnel
from pyremotenode.tasks.ts7400 import Sleep
from pyremotenode.tasks.utils import ListCommand, RunCommand, CheckCommand

__all__ = [
    "RunCommand",
    "CheckCommand",
    "ListCommand",
    "SendLoHBaselines",
    "Sleep",
    "WakeupTask",
    "MTMessageCheck"
]
