import logging

from pyremotenode.tasks import BaseTask


class Sleep(BaseTask):
    def default_action(self, *args, **kwargs):
        logging.debug("Running default action for Sleep")

