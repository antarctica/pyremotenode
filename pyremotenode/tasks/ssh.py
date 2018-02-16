import logging
import re
import shlex
import subprocess

from pyremotenode.tasks import BaseTask


class SshTunnel(BaseTask):
    class __SshTunnel:
        def __init__(self,
                     address,
                     port,
                     user,
                     ppp0_route="false",
                     **kwargs):
            self._tunnel_address = address
            self._tunnel_port = port
            self._tunnel_user = user

            self._ppp0_route = ppp0_route.lower() == "true"

            self._proc = None

            self._re_ps_f_cmd = re.compile(r'^(?:/usr/bin/)?ssh .*[^\s]+@.+')

        def start(self, **kwargs):
            logging.info("Opening AutoSSH tunnel to {0}:{1}".format(self._tunnel_address, self._tunnel_port))
            reverse_specs = []

            for num, dest in enumerate(self._tunnel_port.split(",")):
                reverse_specs.append("-R {}:*:{}".format(30000 + num, dest))

            if self._ppp0_route:
                logging.debug("Running ppp0 default route management command")
                rc = subprocess.call(shlex.split("ip route add {} dev ppp0".format(self._tunnel_address)))
                if rc == 0:
                    logging.info("Created route down ppp0 interface for {}".format(self._tunnel_address))
                else:
                    logging.warning("Failed to manage ppp0 route for {}".format(self._tunnel_address))

            # TODO: Don't run the service as root, sudo allows us to pick up pyrm's credentials
            cmd = ["sudo", "-u", "pyremotenode", "autossh", "-M 40000:40001",
                   "-o", "GSSAPIAuthentication=no",
                   "-o", "PasswordAuthentication=no",
                   "-o", "ServerAliveInterval=10",
                   "-o", "ServerAliveCountMax=5",
                   "-o", "RequestTTY=no"] + \
                reverse_specs + [
                   "-C", "-N", "{0}@{1}".format(self._tunnel_user, self._tunnel_address),
                   ]
            logging.debug("Running command {0}".format(" ".join(cmd)))
            self._proc = subprocess.Popen(cmd)

            # TODO: subprocess cmd process check

            return True

        def stop(self, **kwargs):
            logging.info("Closing AutoSSH tunnel to {0}:{1}".format(self._tunnel_address, self._tunnel_port))
            # TODO: Ensure we're killing with the correct signal (SIGTERM) as we're getting zombies...
            if self._proc:
                self._proc.terminate()

    instance = None

    def __init__(self, **kwargs):
        if not SshTunnel.instance:
            BaseTask.__init__(self, **kwargs)
            SshTunnel.instance = SshTunnel.__SshTunnel(**kwargs)

    def __getattr__(self, item):
        if hasattr(super(SshTunnel, self), item):
            return getattr(super(SshTunnel, self), item)
        return getattr(self.instance, item)

