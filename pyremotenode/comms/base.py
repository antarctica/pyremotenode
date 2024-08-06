
import logging

from pyremotenode.comms.iridium import RudicsConnection, CertusConnection
from pyremotenode.utils import Configuration

# TODO: We need to implement a shared key security system on the web-exposed service
# TODO: This whole implementation is intrisincally tied to the TS7400


class ModemConnection:
    instance = None

    # TODO: This should ideally deal with multiple modem instances based on parameterisation
    def __init__(self, **kwargs):
        logging.debug("ModemConnection constructor access")
        if not ModemConnection.instance:
            cfg = Configuration().config

            impl = RudicsConnection \
                if "type" in cfg["ModemConnection"] and cfg["ModemConnection"]["type"] != "certus" \
                else CertusConnection
            logging.debug("ModemConnection instantiation")
            ModemConnection.instance = impl(cfg)
        else:
            logging.debug("ModemConnection already instantiated")

    def __getattr__(self, item):
        return getattr(self.instance, item)


class ModemConnectionException(Exception):
    pass
