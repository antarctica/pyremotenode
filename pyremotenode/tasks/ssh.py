import logging
import re
import subprocess

from pyremotenode.tasks import BaseTask


class SshTunnel(BaseTask):
    class __SshTunnel:
        def __init__(self,
                     address,
                     port,
                     user,
                     **kwargs):
            self._tunnel_address = address
            self._tunnel_port = port
            self._tunnel_user = user

            self._proc = None

            self._re_ps_f_cmd = re.compile(r'^(?:/usr/bin/)?ssh .*[^\s]+@.+')

        def start(self):
            logging.info("Opening AutoSSH tunnel to {0}:{1}".format(self._tunnel_address, self._tunnel_port))
            cmd = ["autossh", "-M 40000:40001",
                   "-o", "GSSAPIAuthentication=no",
                   "-o", "PasswordAuthentication=no",
                   "-o", "ServerAliveInterval=10",
                   "-o", "ServerAliveCountMax=5",
                   "-R", "{0}:*:22".format(self._tunnel_port),
                   "-C", "-N", "{0}@{1}".format(self._tunnel_user, self._tunnel_address),
                   ]
            logging.debug("Running command {0}".format(" ".join(cmd)))
            self._proc = subprocess.Popen(cmd)

            # TODO: subprocess cmd process check

            return True

        def stop(self):
            logging.info("Closing AutoSSH tunnel to {0}:{1}".format(self._tunnel_address, self._tunnel_port))
            self._proc.terminate()

    instance = None

    def __init__(self, **kwargs):
        if not SshTunnel.instance:
            BaseTask.__init__(self, **kwargs)
            SshTunnel.instance = SshTunnel.__SshTunnel(**kwargs)

    def __getattr__(self, item):
        return getattr(self.instance, item)

