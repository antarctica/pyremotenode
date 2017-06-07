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
    args = a.parse_args()

    cfg = Configuration(args.config).config

    m = MasterSchedule(cfg)
    m.start()
