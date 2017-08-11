## pyremotenode

The aim of this python module is to provide an easy manner by which to 
schedule, monitor and communicate via / with an SBC board of non-denominational 
Linux variety (Windows can go hang for all I care, but it should be 
compatible if possible) via a multitude of transport mediums (potentially)...

### Process / TODO

The process for development will be incremental, based on the foundations 
left by LRAD's development process. However, the aim is that this operates
with an ever more customisable feature set for achieving the above three 
goals. 

This customisability will be increasingly delivered from the point of first
prototype:

- DONE: Integrate LRAD code, strip out messaging (first prototype to demo)
- TODO: Create python resources
- TODO: Implement direct comms between two modems for file transfer: 
    XModem over pyserial example - https://stackoverflow.com/questions/1834247/can-i-use-the-xmodem-protocol-with-pyserial

- TODO: Support several modems (Modem is currently a singleton)

### Development environment

You will need Python v3.4 installed, use `make altinstall` to
install it alongside your own Python3 after building it if it 
does not exist in your repos.

NOTE: This should work with 3.3, but not 3.2 as the scheduler 
needs multithreading

Set up a virtual environment: 

mkvirtualenv -p `which python3.2` -a . -r requirements.txt pyremotenode


