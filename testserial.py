#!/usr/bin/env python3

import serial
import time

s = serial.Serial(
    port="/dev/ttyUSB0",
    baudrate="115200",
    timeout=5,
)
if not s.isOpen():
    s.open()
s.write("AT\r".encode())
#s.flushOutput()

time.sleep(1)
print(s.in_waiting)
while s.in_waiting:
    print(s.readline())
s.close()
