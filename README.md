## pyscheduler

The aim of this python module is to provide an easy manner by which to 
schedule, monitor and communicate via / with an SBC board of non-denominational 
Linux variety (Windows can go hang for all I care, but it should be 
compatible if possible)...

### Process / TODO

The process for development will be incremental, based on the foundations 
left by LRAD's development process. However, the aim is that this operates
with an ever more customisable feature set for achieving the above three 
goals. 

This customisability will be increasingly delivered from the point of first
prototype:

- TODO: Integrate LRAD code, strip out messaging (first prototype to demo)
- TODO: Create installation resources

### Development environment

You will need Python v3.2 installed, use `make altinstall` to
install it alongside your own Python3 after building it. 

Set up a virtual environment: 

mkvirtualenv -p `which python3.2` -a . -r requirements.txt pyscheduler


