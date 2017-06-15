import logging
import re
import shlex
import subprocess

from . import BaseMonitor

RE_OUTPUT = re.compile(r'^.*(ok|warning|critical|invalid)\s*\-.+', flags=re.IGNORECASE)


class Command(BaseMonitor):
    def __init__(self, path, name=None, *args, **kwargs):
        BaseMonitor.__init__(self, *args, **kwargs)
        self._name = name if name else path
        self._args = [path]
        self._proc = None

        for k, v in kwargs.items():
            if k == "scheduler":
                continue
            self._args.append("--{0}".format(k))
            self._args.append(v)
        logging.debug("Command: {0}".format(self._args))

    def monitor(self):
        logging.info("Checking command {0}".format(self._name))
        ret = None

        try:
            ret = subprocess.check_output(args=shlex.split(" ".join(self._args)))
        except subprocess.CalledProcessError as e:
            logging.warning("Got error code {0} and message: {1}".format(e.returncode, e.output))
            return BaseMonitor.INVALID

        logging.debug("Check return output: {0}".format(ret))
        return self.parse_check_output(ret)

    def parse_check_output(self, output):
        status = RE_OUTPUT.match(str(output)).group(1)

        if not status:
            return BaseMonitor.INVALID
        attr = "{0}".format(status.upper())

        logging.debug("Got status output {0}".format(status))

        if hasattr(BaseMonitor, attr):
            return getattr(BaseMonitor, attr)

        return BaseMonitor.INVALID

