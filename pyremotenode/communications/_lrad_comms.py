import logging
import os
import re
import signal
import subprocess
import time

log = logging.getLogger(__name__)


class BaseComms():
    pass




class IridiumComms(BaseComms):
    def __init__(self,
                 *args,
                 device = "ppp0",
                 **kwargs):
        log.debug("Creating {0}".format(__class__.__name__))
        self._device = device
        self.re_ifip = re.compile(r'\s*inet \d+\.\d+\.\d+\.\d+')
        self._interface_path = os.path.join(os.sep, "proc", "sys", "net", "ipv4", "conf", self._device)

        super(IridiumComms, self).__init__(*args, **kwargs)

    def start(self):
        """
        Start the WVDial processing

        :return:
         bool - True for success in starting, False for failure
        """
        # Check whether we have the required interface
        log.debug("Start comms, first checking for interface at {0}".format(self._interface_path))

        if self.is_ready():
            log.info("We have a ready to go {0} interface".format(self._device))
            # TODO: Get process for control if not currently registered (would be odd)
            return True
        else:
            log.info("We have no {0} interface".format(self._device))

            if self._start_wvdial():
                rechecks = 1

                # Validate the connectivity
                while not self.is_ready() \
                        and rechecks <= self._max_checks:
                    log.debug("We have yet to get an interface up on check {0} of {1}".format(rechecks, self._max_checks))
                    time.sleep(self._check_interval)
                    rechecks += 1

                if rechecks == self._max_checks \
                        and not self.is_ready():
                    log.error("We have failed to bring up the {0} interface".format(self._device))
                    return False

                log.info("We have the {0} interface at {1}".format(self._device, self._interface_path))
                return True

        return False

    def is_ready(self):
        if self._proc and os.path.exists(self._interface_path):
            ipline = [ x.strip() for x in
                       subprocess.check_output(["ip", "addr", "show", self._device], universal_newlines=True).split("\n")
                       if self.re_ifip.match(x) ]

            if len(ipline) > 0:
                log.info("Active interface detected with {0}".format(ipline[0]))
                return True
            else:
                log.debug("Interface detected but no IP is present, so not ready to use...")
                return False
        return False

    def stop(self):
        if self._proc:
            log.info("Terminating process with PID {0}".format(self._proc.pid))
            self._proc.terminate()
            del self._proc
            time.sleep(self._wait_to_stop)
        self._terminate_wvdial()

    def _start_wvdial(self):
        log.debug("Starting wvdial and hoping it has a \"square go\" at things...")
        self._proc = subprocess.Popen(["wvdial"])

        # This is the best we can do to assess the state of WVDial without parsing output which is risky as it'll
        # lock this thread up.
        #
        # WVDial will eventually become a zombie if inactive, but will be terminated by the interpreter since it's a
        # child process. This should mean, on successful connection, we are safe to open network activity. If the call
        # fails, we need to trust WVDial to handle the retries (which it usually does) or zombify. If it does zombify,
        # this instantiation of comms will have failed and will error...
        #
        # TODO: Cleanly terminate zombie processes more effectively allowing re-instantiation of comms (low priority)...
        if self._proc.pid:
            # TODO: Check for the existence / instantiation of the ppp child process, if we want to try more than once anyway
            return True
        return False

    @staticmethod
    def _stop_wvdial(self, sig_to_stop, wvdial_pids = []):
        """
        We have no interface, kill wvdial if it exists
        :return: Boolean
        """

        if len(wvdial_pids):
            for pid in wvdial_pids:
                log.debug("PID {0} being given {1}".format(pid, sig_to_stop))
                os.kill(int(pid), sig_to_stop)

        return True

    def _terminate_wvdial(self):
        retries = 0
        sig_to_stop = signal.SIGTERM

        pids = self._wvdial_pids()
        if not len(pids): return True

        while len(pids) and retries < self._max_kill_tries:
            if retries == self._max_kill_tries - 1:
                sig_to_stop = signal.SIGKILL
            self._stop_wvdial(sig_to_stop, pids)
            time.sleep(self._wait_to_stop)
            retries += 1
            log.debug("Attempt {0} to stop WVDial".format(retries))
            pids = self._wvdial_pids()
            if not len(pids): return True

        return False

    @staticmethod
    def _wvdial_pids():
        log.info("Checking for WVDial PIDs")
        wvdial_pids = [y[0]
                       for y in [proc.split()
                                 for proc in
                                 subprocess.check_output(["ps", "-e"], universal_newlines=True).split('\n')
                                 if len(proc.split()) == 4]
                       if y[3].startswith('wvdial')]

        log.info("{0} WVDial PIDs found...".format(len(wvdial_pids)))
        return wvdial_pids


class SSHComms(BaseComms):
    def __init__(self,
                 address,
                 port,
                 user,
                 *args,
                 monitor_port=40108,
                 **kwargs):
        self._tunnel_address = address
        self._tunnel_port = port
        self._tunnel_user = user

        self._re_ps_f_cmd = re.compile(r'^(?:/usr/bin/)?ssh .*[^\s]+@.+')

        super(SSHComms, self).__init__(*args, **kwargs)

    def start(self):
        log.info("Opening AutoSSH tunnel to {0}:{1}".format(self._tunnel_address, self._tunnel_port))
        cmd = ["autossh", "-M 40000:40001",
               "-o", "GSSAPIAuthentication=no",
               "-o", "PasswordAuthentication=no",
               "-o", "ServerAliveInterval=10",
               "-o", "ServerAliveCountMax=5",
               "-R", "{0}:*:22".format(self._tunnel_port),
               "-C", "-N", "{0}@{1}".format(self._tunnel_user, self._tunnel_address),
        ]
        log.debug("Running command {0}".format(" ".join(cmd)))
        self._proc = subprocess.Popen(cmd)
        log.debug("Awaiting confirmation of an SSH process being alive")
        rechecks = 1

        while not self._detect_ssh_tunnel() \
                and rechecks <= self._max_checks:
            log.debug("We have yet to get tunnel up on check {0} of {1}".format(rechecks, self._max_checks))
            time.sleep(self._check_interval)
            rechecks += 1

        if rechecks == self._max_checks \
                and not self._detect_ssh_tunnel():
            log.error("We have failed to bring up the SSH tunnel")
            return False

        log.info("We have an active SSH tunnel (at least once anyway)")
        return True

    def is_ready(self):
        log.debug("Checking to see if SSH tunnel is ready")
        return self._detect_ssh_tunnel()

    def stop(self):
        log.info("Closing AutoSSH tunnel to {0}:{1}".format(self._tunnel_address, self._tunnel_port))
        self._proc.kill()

    def _detect_ssh_tunnel(self):
        if not self._proc:
            return False

        log.info("Detemining if PID {0} has a child SSH tunnel".format(self._proc.pid))
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
            log.info("Have found {0} SSH tunnels active".format(len(ssh_procs)))
            return True
        log.debug("No SSH tunnel connections found")
        return False

