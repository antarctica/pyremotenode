#!/usr/bin/env python
import datetime
import logging

import pynmea2
import serial


def get_time(serial_port,
             baud=4800,
             timeout=1.0,
             time_limit=None):
    com = None
    dt = None
    start = datetime.datetime.utcnow()

    while not dt or (time_limit and (start - datetime.datetime.utcnow()).total_seconds() < time_limit):
        if com is None:
            try:
                com = serial.Serial(
                    serial_port,
                    baudrate=baud,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=timeout,
                    rtscts=True,
                    dsrdtr=True)
            except serial.SerialException:
                logging.error("Could not connect to GPS on {}".format(serial_port))
                continue

        # The stream reader version of this doesn't work with byte IO
        reader = pynmea2.NMEAStreamReader()
        data = com.readline()

        try:
            for msg in reader.next(data.decode()):
                if msg.sentence_type == "RMC":
                    dt = datetime.datetime.combine(
                        msg.datestamp,
                        msg.timestamp
                    )
        except pynmea2.ParseError:
            logging.debug("Issue parsing GPS data")
    return dt

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    while True:
        logging.info(get_time("./ttyGPS", 9600))
