import importlib
import logging


class TaskException(Exception):
    pass


class BaseTask(object):
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        logging.info("__call__ called on {}".format(self.__class__.__name__))
        pass

    def default_action(self, *args, **kwargs):
        raise TaskException("There is no default exception defined for {}".format(self.__name__))


class RudicsConnection(BaseTask):
    def start(self, *args, **kwargs):
        logging.debug("Running start action for RudicsConnection")

    def stop(self, *args, **kwargs):
        logging.debug("Running stop action for RudicsConnection")


class SshTunnel(BaseTask):
    def start(self, *args, **kwargs):
        logging.debug("Running start action for SshTunnel")

    def stop(self, *args, **kwargs):
        logging.debug("Running stop action for SshTunnel")


class Command(BaseTask):
    def default_action(self, *args, **kwargs):
        logging.debug("Running default action for Command")


class SBDMessage(BaseTask):
    def default_action(self, *args, **kwargs):
        logging.debug("Running default action for SBDMessage")


class Sleep(BaseTask):
    def default_action(self, *args, **kwargs):
        logging.debug("Running default action for Sleep")

