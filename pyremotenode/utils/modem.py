import logging
import serial


class SerialModem(object):
    def __init__(self,
                 port,
                 timeout,
                 baud=115200,
                 byte_size=serial.EIGHTBITS,
                 parity=serial.PARITY_NONE,
                 stop_bits=serial.STOPBITS_ONE):
        self._data = serial.Serial()
        self._data.port = port
        self._data.timeout = timeout
        self._data.baudrate = baud
        self._data.byte_size = byte_size
        self._data.parity = parity
        self._data.stop_bits = stop_bits

    def initialise(self):
        try:
            if not self._data.isOpen():
                self._data.open()
            self._data.close()
        except serial.SerialException as e:
            raise SerialModemError('Failed to initialise modem: "{}"; {}'.format(self.ID, str(e)))

    def connect(self):
        raise NotImplementedError

    def send_receive(self, message):
        if not self._data.isOpen():
            raise SerialModemError('Cannot send message; data port is not open')
        self._data.flushInput()
        self._data.write(message.encode('latin-1'))

        logging.debug('Message sent: "{}"'.format(message.strip()))
        reply = self._data.readline().decode('latin-1')
        logging.debug('Message received: "{}"'.format(reply.strip()))

        return reply

    def disconnect(self):
        raise NotImplementedError


class SerialModemError(Exception):
    """Exception for communication errors between computer and local modem"""
    def __init__(self, value):
        self.value = value
        logging.error(value)

    def __str__(self):
        return repr(self.value)
