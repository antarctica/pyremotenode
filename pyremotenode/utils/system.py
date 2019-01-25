import fcntl
import logging
import os
import resource
import sys


def background_fork():
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError as e:
        print("Fork failed: {} ({})".format(e.errno, e.strerror))
        sys.exit(1)

    os.chdir("/")
    os.setsid()
    os.umask(0)

    maxfd = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    if maxfd == resource.RLIM_INFINITY:
        maxfd = 1024

    # Iterate through and close all file descriptors.
    for fd in range(0, maxfd):
        try:
            os.close(fd)
        except OSError:  # ERROR, fd wasn't open to begin with (ignored)
            pass

    if hasattr(os, "devnull"):
        REDIRECT_TO = os.devnull
    else:
        REDIRECT_TO = "/dev/null"
    os.open(REDIRECT_TO, os.O_RDWR)
    os.dup2(0, 1)
    os.dup2(0, 2)

    # NOTE: Normally we should double fork, but not currently doing so


class pid_file:
    def __init__(self, path):
        self._f = None
        self._path = path

        if self._path:
            if not os.path.isdir(os.path.dirname(path)):
                logging.debug("Creating directory {} for PID".format(path))
                os.makedirs(os.path.dirname(path), exist_ok=True)

            if os.path.exists(path):
                raise PidFileExistsError

            self._f = open(path, mode='w')
            fcntl.lockf(self._f, fcntl.LOCK_EX)
            self._f.write(str(os.getpid()))
            self._f.flush()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._path:
            self._f.close()
            os.unlink(self._path)


class PidFileExistsError(IOError):
    pass
