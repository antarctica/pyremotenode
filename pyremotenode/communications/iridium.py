import logging
import os
import re
import shlex
import signal
import subprocess
import time

from . import BaseComms, Modem, CommsRunError

__all__ = [
    "RudicsComms",
    "SbdComms",
    "SendSbdMessage",
]


class IridiumComms(BaseComms):
    def __init__(self, *args, **kwargs):
        BaseComms.__init__(self, *args, **kwargs)
        # TODO: self._modem is not used here as RudicsComms implementation is command driven however
        # it might afford us more control to have a direct modem implementation: this will be feature branched...


class RudicsComms(IridiumComms):
    def __init__(self,
                 *args,
                 device = "ppp0",
                 **kwargs):
        IridiumComms.__init__(self, *args, **kwargs)

        logging.debug("Creating {0}".format(__class__.__name__))
        self._device = device
        self.re_ifip = re.compile(r'\s*inet \d+\.\d+\.\d+\.\d+')
        self._interface_path = os.path.join(os.sep, "proc", "sys", "net", "ipv4", "conf", self._device)

    def start(self):
        """
        Start the WVDial processing

        :return:
         bool - True for success in starting, False for failure
        """
        # Check whether we have the required interface
        logging.debug("Start comms, first checking for interface at {0}".format(self._interface_path))

        if self.is_ready():
            logging.info("We have a ready to go {0} interface".format(self._device))
            # TODO: Get process for control if not currently registered (would be odd)
            return True
        else:
            logging.info("We have no {0} interface".format(self._device))

            if self._start_wvdial():
                rechecks = 1
                # Validate the connectivity

                while not self.is_ready() \
                        and rechecks <= self._max_checks:
                    logging.debug("We have yet to get an interface up on check {0} of {1}".format(rechecks, self._max_checks))
                    # TODO: This needs threading
                    time.sleep(self._check_interval)
                    rechecks += 1

                if rechecks == self._max_checks \
                        and not self.is_ready():
                    logging.error("We have failed to bring up the {0} interface".format(self._device))
                    return False

                logging.info("We have the {0} interface at {1}".format(self._device, self._interface_path))
                return True

        return False

    def ready(self):
        if self._proc and os.path.exists(self._interface_path):
            ipline = [ x.strip() for x in
                       subprocess.check_output(["ip", "addr", "show", self._device], universal_newlines=True).split("\n")
                       if self.re_ifip.match(x) ]

            if len(ipline) > 0:
                logging.info("Active interface detected with {0}".format(ipline[0]))
                return True
            else:
                logging.debug("Interface detected but no IP is present, so not ready to use...")
                return False
        return False

    def stop(self):
        if self._proc:
            logging.info("Terminating process with PID {0}".format(self._proc.pid))
            self._proc.terminate()
            del self._proc
            time.sleep(self._wait_to_stop)
        self._terminate_wvdial()

    def _start_wvdial(self):
        logging.debug("Starting wvdial and hoping it has a \"square go\" at things...")
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

    def _stop_wvdial(self, sig_to_stop, wvdial_pids = []):
        """
        We have no interface, kill wvdial if it exists
        :return: Boolean
        """

        if len(wvdial_pids):
            for pid in wvdial_pids:
                logging.debug("PID {0} being given {1}".format(pid, sig_to_stop))
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
            logging.debug("Attempt {0} to stop WVDial".format(retries))
            pids = self._wvdial_pids()
            if not len(pids): return True

        return False

    def _wvdial_pids(self):
        logging.info("Checking for WVDial PIDs")
        wvdial_pids = [y[0]
                       for y in [proc.split()
                                 for proc in
                                 subprocess.check_output(["ps", "-e"], universal_newlines=True).split('\n')
                                 if len(proc.split()) == 4]
                       if y[3].startswith('wvdial')]

        logging.info("{0} WVDial PIDs found...".format(len(wvdial_pids)))
        return wvdial_pids


#    TODO: So the invocation / configuration methodology doesn't really fit this
#    as it's a repeating action - ready will be called by inference from BaseComms
#    but that's just sad. Refactor definitely required of that whole element (though
#    that was becoming suspected during the Venom port which suffered
#    the same kind of issue
class SbdSendComms(IridiumComms):
    def __init__(self, path, *args, **kwargs):
        IridiumComms.__init__(self, *args, **kwargs)
        modem_args = self._scheduler.get_config('modem')

        self._modem = Modem(**modem_args)
        self._message_command = [path]

        self.re_sbdix = re.compile(r'\+SBDIX:\s*(?:([^,]+),){5}([^,]+)')

    def ready(self):
        logging.debug("Running message command")
        message = None

        try:
            message = str(subprocess.check_output(args=shlex.split(" ".join(self._message_command)))).strip()
        except subprocess.CalledProcessError as e:
            logging.warning("Got error code {0} and message: {1}".format(e.returncode, e.output))
            return False

        msg_bytes = message.encode("latin-1")
        msg_checksum = self._sbd_checksum(msg_bytes)
        msg_isu_store = "AT+SBDWB={}\n".format(len(msg_bytes))

        logging.debug("Sending SBD message")

        try:
            self._modem.initialise()

            if self._modem.send_receive_messages(msg_isu_store) == "READY":
                ec = self._modem.send_receive_messages(msg_bytes + msg_checksum)

                if int(ec) == 0:
                    sc = self._modem.send_receive_messages("AT+SBDIX")
                    sc_parse = self.re_sbdix.match(sc)

                    if not sc_parse:
                        # TODO: We don't have a valid response :-(
                        pass
                    else:
                        # +SBDIX:<MO status>,<MOMSN>,<MT status>,<MTMSN>,<MT length>,<MT queued>
                        mo_status, mo_msn, mt_status, mt_msn, mt_length, mt_queued = \
                            sc_parse.groups()

                        # TODO: Process mo_status and action appropriately
                        # TODO: Process received message (or not) via mt_status
                else:
                    # TODO: Oh shit, we sent something invalid
                    pass
        except CommsRunError as ce:
            logging.error("Problem with the modem: {}".format(ce))
        finally:
            self._modem.disconnect()

    def _sbd_checksum(self, msg):
        total = 0

        for i, b in enumerate(msg):
            total += b

        chksum = total & 0xFFFF
        return chksum

