import logging

from pyremotenode.tasks import BaseTask


class SshTunnel(BaseTask):
    class __SshTunnel:
        def __init__(self, *args, **kwargs):
            BaseTask.__init__(self, *args, **kwargs)

        def start(self, *args, **kwargs):
            logging.debug("Running start action for SshTunnel")

        def stop(self, *args, **kwargs):
            logging.debug("Running stop action for SshTunnel")

    instance = None

    def __init__(self, *args, **kwargs):
        if not self.instance:
            self.instance = SshTunnel.__SshTunnel(*args, **kwargs)

    def __getattr__(self, item):
        return getattr(self.instance, item)

