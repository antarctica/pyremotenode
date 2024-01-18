import logging
import re
import os
import shlex
import subprocess

from pyremotenode.tasks import BaseTask, TaskException

RE_OUTPUT = re.compile(r'^.*(ok|warning|critical|invalid)\s*\-.+', flags=re.IGNORECASE)

# TODO: Make Command / BaseTask responsible for initiating the activities that are configured within an action


class Command(BaseTask):
    def __init__(self, path, name=None, **kwargs):
        BaseTask.__init__(self, **kwargs)
        self._name = name if name else path
        self._args = [path]
        self._proc = None
        self._output = None

        for k, v in kwargs.items():
            if k in ["id", "scheduler", "binary"]:
                continue
            self._args.append("--{0}".format(k))
            self._args.append(v)
        logging.debug("Command: {0}".format(self._args))

    def default_action(self, **kwargs):
        logging.info("Checking command {0}".format(self._name))
        ret = None

        try:
            ret = subprocess.check_output(
                args=shlex.split(" ".join(self._args)),
                universal_newlines=not self.binary)
        except subprocess.CalledProcessError as e:
            if not self.binary:
                logging.warning("Got error code {0} and message: {1}".format(e.returncode, e.output))
            # TODO: Evaluate how this will be handled in the end
            raise TaskException("The called command failed with an out of bound return code...")

        if not self.binary:
            logging.info("Command return output: {0}".format(ret))

        return self._process_cmd_output(ret)

    def _process_cmd_output(self, ret):
        raise NotImplementedError

    @property
    def message(self):
        return self._output


class RunCommand(Command):
    def __init__(self, *args, **kwargs):
        Command.__init__(self, *args, **kwargs)

    def _process_cmd_output(self, output):
        self._output = output
        return self.OK


class CheckCommand(Command):
    def __init__(self, *args, **kwargs):
        Command.__init__(self, *args, **kwargs)

    def _process_cmd_output(self, output):
        self._output = output.strip()

        try:
            status = RE_OUTPUT.match(str(output)).group(1)
        except Exception:
            status = None

        if not status:
            raise TaskException("An unparseable status was received from the check: {}".format(str(output)))
        attr = "{0}".format(status.upper())

        logging.debug("Got valid status output: {0}".format(status))

        # TODO: Change this to have configuration parsing in the BaseTask
        if hasattr(self, attr):
            return getattr(self, attr)

        return self.INVALID


class ListCommand(Command):
    def __init__(self, *args, **kwargs):
        Command.__init__(self, *args, **kwargs)

    def _process_cmd_output(self, output):
        filelist = []

        for f in [l.strip() for l in output.split(os.linesep) if len(l)]:
            if os.path.exists(f) and os.path.isfile(f):
                logging.debug("Listed {}".format(f))
                filelist.append(f)
            else:
                logging.warning("Non-existent file dropped {}".format(f))

        self._output = filelist

        if not len(filelist):
            return self.WARNING
        return self.OK
