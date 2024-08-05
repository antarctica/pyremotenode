from abc import ABCMeta, abstractmethod
import logging

from pyremotenode.comms.connections import RudicsConnection
from pyremotenode.utils import Configuration

# TODO: We need to implement a shared key security system on the web-exposed service
# TODO: This whole implementation is intrisincally tied to the TS7400


class ConnectionInterface(metaclass=ABCMeta):
    @abstractmethod
    def get_system_time(self):
        raise NotImplementedError("get_system_time not implemented")

    @abstractmethod
    def send_file(self):
        raise NotImplementedError("send_file not implemented")

    @abstractmethod
    def send_message(self):
        raise NotImplementedError("send_message not implemented")

    @abstractmethod
    def run(self):
        raise NotImplementedError("run not implemented")


class ModemConnection:
    instance = None

    # TODO: This should ideally deal with multiple modem instances based on parameterisation
    def __init__(self, **kwargs):
        logging.debug("ModemConnection constructor access")
        if not ModemConnection.instance:
            logging.debug("ModemConnection instantiation")
            ModemConnection.instance = RudicsConnection()
        else:
            logging.debug("ModemConnection already instantiated")

    def __getattr__(self, item):
        return getattr(self.instance, item)

    @staticmethod
    def calculate_sbd_checksum(payload):
        chk = bytearray()
        s = sum(payload)
        chk.append((s & 0xFF00) >> 8)
        chk.append(s & 0xFF)
        return chk
