import argparse
import logging

from pyremotenode import MasterSchedule
from pyremotenode.utils import Configuration, setup_logging

log = setup_logging(__name__)
logging.info("PyRemoteNode")

if __name__ == '__main__':
    a = argparse.ArgumentParser()
    a.add_argument("config", help="Configuration to use for running remote node",
                   type=Configuration.check_file)
    a.add_argument("--start-when-fail", "-s", help="Start even if initial monitor checks fail",
                   action="store_true", default=False)
    args = a.parse_args()

    cfg = Configuration(args.config).config

    m = MasterSchedule(cfg,
                       start_when_fail=args.start_when_fail)
    m.run()
