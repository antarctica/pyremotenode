#!/usr/bin/env python3

#
# comms
#
# Author: James Byrne
#
# Script to boot up the modem in an ad-hoc fashion,
# as well as bring up the tunnel to Cambridge

import sys
import time

from argparse import ArgumentParser
from datetime import datetime, timedelta

from basscheduler import setup_logging
from basscheduler.comms import IridiumComms, SSHComms

log = setup_logging(__name__)

if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("-m", "--minutes", dest="minutes", type=int, default=30)
    parser.add_argument("-i", "--skip-iridium", dest="skip_iridium", action="store_true", default=False)
    parser.add_argument("-s", "--server", dest="server", required=True)
    parser.add_argument("-u", "--user", dest="user", required=True)
    parser.add_argument("-p", "--port", dest="port", type=int, default=40109)
    args = parser.parse_args()

    ir = IridiumComms(
        max_start_checks=18,
        start_check_interval=10,
    )
    ssh = SSHComms(
        args.server,
        args.port,
        args.user,
        max_start_checks=12,
        start_check_interval=10
    )

    log.info("Starting Iridium")

    # Bring up the Iridium, then the SSH
    #
    # The basscheduler.comms module handles all this, but the basic premise is that we try hard
    # everything up and then attempt to kill it gracefully if possible
    if args.skip_iridium or (ir.start() and ir.is_ready()):
        log.info("Starting SSH tunnel")

        if not ssh.start():
            log.error("Failed to start SSH")
            if not args.skip_iridium: ir.stop()
            sys.exit(1)

        now_time = datetime.now()
        end_time = datetime.now() + timedelta(minutes=args.minutes)

        # We have our connections, let's just keep this thread rolling until it's
        # time to give up
        while now_time < end_time:
            log.debug("{0} is prior to end time {1}".format(now_time, end_time))
            time.sleep(60)
            now_time = datetime.now()

        # Now it's time to give up
        log.info("Window has ended ({0} vs end time of {1}), shutting down...".format(now_time, end_time))
        ssh.stop()
        if not args.skip_iridium: ir.stop()
    else:
        log.error("Failed to start Iridium")
        sys.exit(1)
