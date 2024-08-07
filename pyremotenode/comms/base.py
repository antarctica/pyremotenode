
import logging

from pyremotenode.comms.iridium import RudicsConnection, CertusConnection
from pyremotenode.utils import Configuration

# TODO: We need to implement a shared key security system on the web-exposed service
# TODO: This whole implementation is intrisincally tied to the TS7400


class ModemConnection:
    _instance = None

    # TODO: This should ideally deal with multiple modem instances based on parameterisation
    def __init__(self, **kwargs):
        logging.debug("ModemConnection constructor access")
        if not ModemConnection._instance:
            cfg = Configuration().config

            impl = RudicsConnection \
                if "type" not in cfg["ModemConnection"] or cfg["ModemConnection"]["type"] != "certus" \
                else CertusConnection
            logging.debug("ModemConnection instantiation")
            ModemConnection._instance = impl(cfg)
        else:
            logging.debug("ModemConnection already instantiated")

    def __getattr__(self, item):
        return getattr(self._instance, item)

    @property
    def instance(self):
        return self._instance


class ModemConnectionException(Exception):
    pass
