from configparser import ConfigParser

import gzip
import logging
import os
import re
import subprocess

from datetime import datetime
from glob import glob
from shutil import rmtree
from threading import Thread

log = logging.getLogger(__name__)


class DataAcquisitor:
    """
    class lrad.DataAcquisitor

    This class handles post-processing of LoH GPS data to an output location
    in compressed form (via TEQC and GZIP)

    The instantiation of the processing results in a new thread that will allow
    the caller to undertake monitoring and other activities whilst this is happening.
    It is important to note that transfers may be incomplete at the set level should
    they be timed inappropriately

    Further utilising transport compression will further assist transfers
    """

    def __init__(self,
                 end = False,
                 datadir = os.path.join(os.sep, "data", "loh", "data"),
                 destdir = os.path.join(os.sep, "data", "lrad", "output"),
                 interval = 30):
        log.debug("Creating {0}".format(__class__.__name__))

        self._configurations = {}
        self._datadir = datadir
        self._destdir = destdir
        self._end = end
        self._sched_end = None
        self._interval = interval

        self.__thread = Thread(
            name = __class__.__name__,
            target=self.acquire)

        self.configure()

    def configure(self):
        """
        configure

        Grab all LoH site configurations and ensure our data endpoints are ready to use
        :return:
         none
        """
        # Check we have data directory
        if not os.path.exists(self._datadir):
            raise NotADirectoryError("Data directory {0} missing".format(self._datadir))
        if not os.path.exists(self._destdir):
            raise NotADirectoryError("Destination directory {0} missing".format(self._destdir))

        # Read the loh configurations - parse the crontab for loh to identify running jobs
        # TODO: Configurations only parsed on creation of the daemon currently
        configurations = []
        re_job = re.compile(r'^[\s\*\d]+\/usr\/.*python3? [\/~].*\/loh_sync\.py --config-file ([^\s]+\.cfg) ')
        # TODO: CalledProcessError
        for line in subprocess.check_output(["crontab", "-l"], universal_newlines=True).split('\n'):
            job_match = re_job.search(line)

            if job_match:
                config_file = os.path.expanduser(job_match.group(1))
                log.info("Using config from job {0}".format(config_file))
                confparser = ConfigParser(inline_comment_prefixes=';')
                confparser.read(config_file)

                #(start, end) = parse_schedule_conf(self._confparser)[0]
                #if not self._sched_end or end > self._sched_end:
                #    self._sched_end = time(
                #        hour=end.hour,
                #        minute=end.minute)

                # TODO: Sanitisation of configuration?
                conf_dict = dict([(key, confparser.items(key)) for key in
                           filter(lambda x: x.startswith('site.'), confparser.keys())])
                for (key, value) in conf_dict.items():
                    log.debug("Adding configuration {0}".format(key))
                    if key in self._configurations.keys():
                        raise DuplicateSiteError()
                    self._configurations[key] = value

    def wait(self, timeout=None):
        """
        wait

        Calling thread will await completion of processing, depending on
        whether or not the timeout is set, in which case the caller can
        undertake further activity if something is wrong with the processing

        :param timeout: timeout for thread join
        :return:
        """
        if self.__thread.is_alive():
            self.__thread.join(timeout)

    def start(self):
        """
        start

        Start data processing
        :return:
        """
        if not self.__thread.is_alive():
            self.__thread.start()

    def acquire(self):
        """ Start the acquisitor thread

        This thread performs the following functions:
            - Inspects the state of cron to determine up to date VHF stations
            - Grab the latest data
            - Process the reduction of observables to reduce size
            - Store files ready for pickup
        """
        today = datetime.now()
        current_year = str(today.year)
        datestr = today.strftime("%Y%m%d")
        dateshort = today.strftime("%y")

        # TODO: Optimisation, numerous calls are unnecessary in this block
        # Run through each site.llnn configuration from LoH
        for (site, details) in self._configurations.items():
            log.debug("Checking site {0}".format(site))
            data_path = os.path.join(self._datadir, current_year, site.split(".")[1])
            output_path = os.path.join(self._destdir, current_year, site)

            # We manage the output paths as required
            if not os.path.isdir(output_path):
                log.debug("Creating {0}".format(output_path))
                os.makedirs(output_path)

            # Check the output directory for NAV files that exclude the input
            available_files = [ os.path.basename(file)
                                for file
                                in glob(os.path.join(data_path, "{0}*".format(site.split(".")[1]))) ]
            converted_files = [ os.path.basename(file).split(".")[0]
                                for file
                                in glob(os.path.join(output_path, "{0}*.{1}n".format(site.split(".")[1], dateshort))) ]

            todo_files = [ todo for todo in available_files
                           if todo.split(".")[0] not in converted_files ]

            # We have files to process!
            if len(todo_files):
                log.info("Converting {0} files for {1}".format(len(todo_files), site))

                temp_path = os.path.join(os.sep, "tmp", site, datestr)
                if not os.path.isdir(temp_path):
                    log.debug("Creating {0}".format(temp_path))
                    os.makedirs(temp_path)

                for todo in todo_files:
                    log.debug("Converting {0}".format(os.path.join(data_path, todo)))
                    obs_file = "{0}.17o".format(todo.split(".")[0])
                    zip_file = "{0}.17o.gz".format(todo.split(".")[0])
                    nav_file = "{0}.17n".format(todo.split(".")[0])

                    # We can output
                    with gzip.open(os.path.join(temp_path, zip_file), "wb") as z:
                        log.debug("Compressing observables to {0}".format(os.path.join(temp_path, zip_file)))
                        subprocess.call("teqc -O.dec 120 +nav {0} {1} > {2}".format(
                            os.path.join(temp_path, nav_file),
                            os.path.join(data_path, todo),
                            os.path.join(temp_path, obs_file)),
                            shell = True
                        )
                        with open(os.path.join(temp_path, obs_file), "rb") as o:
                            z.writelines(o)

                    log.debug("Moving zipped {0} and {1} to {2}".format(zip_file, nav_file, output_path))
                    subprocess.call(["mv", os.path.join(temp_path, zip_file), os.path.join(output_path, zip_file)])
                    subprocess.call(["mv", os.path.join(temp_path, nav_file), os.path.join(output_path, nav_file)])

                log.debug("Removing {0}".format(temp_path))
                rmtree(temp_path)


class DuplicateSiteError(KeyError):
    pass