import logging
import os
import re
import shlex
import signal
import subprocess
import threading as t
import time as tm

from pyremotenode.tasks import BaseTask


class ModemConnection(object):
    class __ModemConnection:
        modem_lock = t.Lock()       # Lock message buffer
        running = False

        def __init__(self):
            self._thread = None

        def start(self):
            # TODO: Draft
            if not self._thread:
                self._thread = t.Thread(name=self.__class__.__name__, target=self.run)
                self._thread.setDaemon(True)
                self._thread.start()

        def run(self):
            while self.running:
                logging.debug("{} thread running...".format(self.__class__.__name__))
                tm.sleep(2)

        @property
        def get_modem_lock(self):
            return self.modem_lock

    instance = None

    def __init__(self, **kwargs):
        if not self.instance:
            self.instance = ModemConnection.__ModemConnection(**kwargs)

    def __getattr__(self, item):
        return getattr(self.instance, item)


class RudicsConnection(BaseTask):
    class __RudicsConnection:
        def __init__(self,
                     device="ppp0",
                     max_checks=3,
                     check_interval=20,
                     watch_interval=30,
                     **kwargs):
            self.modem = ModemConnection()

            logging.debug("Creating {0}".format(self.__class__.__name__))
            self._device = device
            self.re_ifip = re.compile(r'\s*inet \d+\.\d+\.\d+\.\d+')
            self._interface_path = os.path.join(os.sep, "proc", "sys", "net", "ipv4", "conf", self._device)
            self._proc = None
            self._thread = None
            self.running = False

            self.max_checks = max_checks
            self.check_interval = check_interval
            self.watch_interval = watch_interval

        def start(self):
            logging.debug("Running start action for RudicsConnection")
            self._thread = t.Thread(name=self.__class__.__name__, target=self.watch)

            # We only need the exclusive use of the modem for WVDial, we don't actually use it via ModemConnection
            self.modem.modem_lock.acquire()

            logging.debug("Start comms, first checking for interface at {0}".format(self._interface_path))
            success = False

            if self.ready():
                logging.info("We have a ready to go {0} interface".format(self._device))
                # TODO: This would be an extremely weird situation, not necessarily successful
                success = True
            else:
                logging.info("We have no {0} interface".format(self._device))

                if self._start_wvdial():
                    self.running = True
                    self._thread.start()
                    success = True
            return success

        def ready(self):
            if self._proc and os.path.exists(self._interface_path):
                ipline = [x.strip() for x in
                          subprocess.check_output(["ip", "addr", "show", self._device], universal_newlines=True).split(
                              "\n")
                          if self.re_ifip.match(x)]

                if len(ipline) > 0:
                    logging.info("Active interface detected with {0}".format(ipline[0]))
                    return True
                else:
                    logging.debug("Interface detected but no IP is present, so not ready to use...")
                    return False
            return False

        def watch(self):
            while self.running:
                rechecks = 1
                while not self.ready() \
                        and rechecks <= self.max_checks:
                    logging.debug(
                        "We have yet to get an interface up on check {0} of {1}".format(rechecks, self.max_checks))
                    tm.sleep(self.check_interval)
                    rechecks += 1

                if rechecks >= self.max_checks \
                        and not self.ready():
                    logging.warning("We have failed to bring up the {0} interface".format(self._device))
                    # TODO: The WVDial process has failed to arrange an interface, we should kill the whole thing
                    self.running = False
                    self.stop()
                else:
                    logging.info("We have the {0} interface at {1}".format(self._device, self._interface_path))
                    tm.sleep(self.watch_interval)

        def stop(self):
            logging.debug("Running stop action for RudicsConnection")

            if self._proc:
                logging.info("Terminating process with PID {0}".format(self._proc.pid))
                self._proc.terminate()
                self._proc = None
                tm.sleep(self._wait_to_stop)
            self._terminate_wvdial()

        def _start_wvdial(self):
            logging.debug("Starting wvdial and hoping it has a \"square go\" at things...")
            self._proc = subprocess.Popen(shlex.split("pppd file /etc/ppp/peers/iridium"))

            # WVDial will eventually become a zombie if inactive
            # TODO: Cleanly terminate zombie processes more effectively allowing re-instantiation of comms (low priority)...
            if self._proc.pid:
                # TODO: Check for the existence / instantiation of the ppp child process, if we want to try more than once anyway
                return True
            return False

        def _stop_wvdial(self, sig_to_stop, wvdial_pids=[]):
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
            if not len(pids):
                self.modem.modem_lock.release()
                return True

            while len(pids) and retries < self._max_kill_tries:
                if retries == self._max_kill_tries - 1:
                    sig_to_stop = signal.SIGKILL
                self._stop_wvdial(sig_to_stop, pids)
                tm.sleep(self._wait_to_stop)
                retries += 1
                logging.debug("Attempt {0} to stop WVDial".format(retries))
                pids = self._wvdial_pids()
                if not len(pids):
                    self.modem.modem_lock.release()
                    return True

            self.modem.modem_lock.release()
            return False

        def _wvdial_pids(self):
            logging.info("Checking for WVDial PIDs")
            wvdial_pids = [y[0]
                           for y in [proc.split()
                                     for proc in
                                     subprocess.check_output(["ps", "-e"], universal_newlines=True).split('\n')
                                     if len(proc.split()) == 4]
                           if y[3].startswith('pppd')]

            logging.info("{0} WVDial PIDs found...".format(len(wvdial_pids)))
            return wvdial_pids

    instance = None

    def __init__(self, **kwargs):
        if not RudicsConnection.instance:
            BaseTask.__init__(self, **kwargs)
            RudicsConnection.instance = RudicsConnection.__RudicsConnection(**kwargs)

    def __getattr__(self, item):
        return getattr(self.instance, item)


class SBDMessage(BaseTask):
    def __init__(self, **kwargs):
        BaseTask.__init__(self, **kwargs)
        self.modem = ModemConnection()

    def default_action(self, **kwargs):
        logging.debug("Running default action for SBDMessage")

        # TODO: Queue messages against the modem
        self.modem.start()
        # TODO: Catch any errors and reset the modem

