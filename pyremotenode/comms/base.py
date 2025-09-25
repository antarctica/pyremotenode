
import logging

import pyremotenode.comms.iridium
from pyremotenode.utils import Configuration


class ModemConnection:
    _instance = None

    # TODO: This should ideally deal with multiple modem instances based on parameterisation
    def __init__(self, **kwargs):
        logging.debug("ModemConnection constructor access")
        if not ModemConnection._instance:
            cfg = Configuration().config

            impl = pyremotenode.comms.iridium.RudicsConnection \
                if "type" not in cfg["ModemConnection"] or cfg["ModemConnection"]["type"] != "certus" \
                else pyremotenode.comms.iridium.CertusConnection
            ModemConnection._instance = impl(cfg)

    def __getattr__(self, item):
        return getattr(self._instance, item)

    @property
    def instance(self):
        return self._instance


class ModemConnectionException(Exception):
    pass
