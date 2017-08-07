#!/bin/bash

# Command line runner for testing the pyremotenode service.
#
#    The service should be invoked via systemd or something similar, this merely
#    starts up the service and sorts out anything required for running off the
#    command line.

/usr/bin/env python -m pyremotenode example.conf