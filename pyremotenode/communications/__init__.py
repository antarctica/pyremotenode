import logging
import serial

from pyremotenode.base import BaseItem


class BaseComms(BaseItem):
    def __init__(self, *args, **kwargs):
        BaseItem.__init__(self, *args, **kwargs)

    def action(self, name):
        logging.debug("Initiating item action: {0}".format(name))

        if name == 'start':
            return self.start()
        elif name == 'end':
            return self.stop()
        return self.ready()

    def start(self):
        raise NotImplementedError

    def ready(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError


# TODO: We should handle multiple modems, this singleton is a hard limit on functionality
class Modem(object):
    class __Modem:
        def __init__(self,
                     port,
                     #timeout = 60,
                     baud = 115200,
                     bytesize = "EIGHTBITS",
                     parity = "PARITY_NONE",
                     stopbits = "STOPBITS_ONE"):
            self._data = serial.Serial()
            self._data.port = port
            # TODO: Timeout should not be used with readline
            #self._data.timeout = float(timeout)
            self._data.baudrate = baud

            if hasattr(serial, bytesize):
                self._data.bytesize = getattr(serial, bytesize)
            else:
                raise AttributeError

            if hasattr(serial, parity):
                self._data.parity = getattr(serial, parity)
            else:
                raise AttributeError

            if hasattr(serial, stopbits):
                self._data.stopbits = getattr(serial, stopbits)
            else:
                raise AttributeError

        def initialise(self):
            try:
                if not self._data.isOpen():
                    self._data.open()
            except serial.SerialException as e:
                raise CommsRunError('Failed to initialise modem: "{}"; {}'.format(self._data.port, str(e)))

        def send_receive_messages(self  , message):
            if not self._data.isOpen():
                raise CommsRunError('Cannot send message; data port is not open')
            self._data.flushInput()
            self._data.write(message.encode('latin-1'))

            logging.debug('Message sent: "{}"'.format(message.strip()))
            reply = self._data.readline().decode('latin-1')
            logging.debug('Message received: "{}"'.format(reply.strip()))
            return reply

        def disconnect(self):
            if self._data.isOpen():
                self._data.close()

    instance = None

    def __init__(self, *args, **kwargs):
        if not Modem.instance:
            Modem.instance = Modem.__Modem(*args, **kwargs)

    def __getattr__(self, item):
        return getattr(self.instance, item)


class CommsConfigureError(Exception):
    pass


class CommsRunError(Exception):
    pass




