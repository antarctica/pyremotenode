import logging
import os
import re
import subprocess
import time

from tempfile import NamedTemporaryFile

from . import BaseComms

__all__ = [
    "SshTunnel",
]


class SshTunnel(BaseComms):
    def __init__(self,
                 address,
                 port,
                 user,
                 *args,
                 **kwargs):
        BaseComms.__init__(self, *args, **kwargs)

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
        logging.debug("Awaiting confirmation of an SSH process being alive")
        rechecks = 1

        while not self._detect_ssh_tunnel() \
                and rechecks <= self._max_checks:
            logging.debug("We have yet to get tunnel up on check {0} of {1}".format(rechecks, self._max_checks))
            # TODO: This needs threading
            time.sleep(self._check_interval)
            rechecks += 1

        if rechecks == self._max_checks \
                and not self._detect_ssh_tunnel():
            logging.error("We have failed to bring up the SSH tunnel")
            return False

        logging.info("We have an active SSH tunnel (at least once anyway)")
        return True

    def ready(self):
        logging.debug("Checking to see if SSH tunnel is ready")
        return self._detect_ssh_tunnel()

    def stop(self):
        logging.info("Closing AutoSSH tunnel to {0}:{1}".format(self._tunnel_address, self._tunnel_port))
        self._proc.terminate()

    def _detect_ssh_tunnel(self):
        if not self._proc:
            return False

        logging.info("Detemining if PID {0} has a child SSH tunnel".format(self._proc.pid))

        ssh_procs = []
        for proc in [ x for x in subprocess.check_output("ps -f", shell=True, universal_newlines=True).split("\n")
                      if x.find("ssh") != -1 ]:
            if not len(proc): continue
            if proc.startswith("UID"): continue

            details = proc.split()
            (pid, parent, command) = (details[1], details[2], " ".join(details[7:]))

            if int(parent) == self._proc.pid \
                and self._re_ps_f_cmd.match(command):
                ssh_procs.append(pid)

        if len(ssh_procs):
            logging.info("Have found {0} SSH tunnels active".format(len(ssh_procs)))
            return True

        logging.debug("No SSH tunnel connections found")
        return False

    @staticmethod
    def send_msg_file(message, user, address, destination, timeout=120):
        logging.info("Sending message file via SSH")
        success = False

        with NamedTemporaryFile(delete=False) as tmp:
            tmp.write(bytes(message.encode("utf8")))

            # Python 3.2 does not contain timeout functionality but it's too late to consider 3.4 at this stage

            try:
                cmd = ["rsync", "-az",
                       "{0}".format(tmp.name), "{0}@{1}:{2}".format(user, address, destination)]
                rc = int(subprocess.call(cmd))
                if rc != 0:
                    logging.error("Failed to send message file with return code {0}".format(rc))
                else:
                    success = True
            except subprocess.CalledProcessError:
                logging.error("Failed to send message file")
            finally:
                os.unlink(tmp.name)
        return success

    @staticmethod
    def send_zip_file(zipfile, user, address, destination):
        logging.info("Sending message file via SSH")
        success = False

        try:
            cmd = ["rsync", "-a",
                   "{0}".format(zipfile), "{0}@{1}:{2}".format(user, address, destination)]
            rc = int(subprocess.call(cmd))
            if rc != 0:
                logging.error("Failed to send message file with return code {0}".format(rc))
            else:
                success = True
        except subprocess.CalledProcessError:
            logging.error("Failed to send message file")

        return success
