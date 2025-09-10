## pyremotenode

The aim of this python module is to provide an easy manner by which to 
schedule, monitor and communicate via / with an SBC board of non-denominational 
 variety via a multitude of transport mediums (potentially, Iridium at present).

## Development environment

Currently we use small-board computers running Debian and Python 3.5 for this, 
such as [the TS7400v2](https://www.embeddedts.com/products/TS-7400-V2). This is 
undoubtedly the best tested board with the utility, but any should potentially work.

### Process to run

1. Create a venv, preferably using a suitably old version of python
2. Install the library `python setup.py install` or what-have-you
3. Use `run_pyremotenode -np -n -v [config]` to run the software

### Current development tasks

In the years since I wrote this, Iridium technology and applications for this 
tool have remained relevant, but vastly changed. The feature set is now (finally)
getting some movement towards being more generic. 

The target is to converge on using this to:

* Schedule tasks on any SBC / suitably microcomputer
* Allow easy communication via Iridium (Certus IMT, though at time of writing 
 SBD and RUDICS are still catered for)
* Provide an easy-ish interface to link task scheduling and comms together

## A personal note

This was written for the British Antarctic Survey during our shutdown of Halley in 
2017, in a rush. This is why it's written in python, because it allowed for a very fast
development process. It was then used to process and relay GPS data on the 
[Brunt Ice Shelf Movement](https://www.bas.ac.uk/project/brunt-ice-shelf-movement/) 
during the winter months even when base power wasn't available (using solar/wind)
based on a few improvements over that time. It's also been used since for some other
experiments running on low power SBCs.

I would like to have written this properly for low power embedded systems at the 
time, but there was no opportunity. If we could change this to be designed in a more 
appropriate language (e.g. Rust), I'm well keen. As it is, it still has applications 
to remote field sites, as well as continues to run at Halley, and I don't have 
time to change that.

Please do get in contact if you're interested in developing this further, or have 
a crack at this repo! I'm really keen to trial other boards with better OS's, as
well as port it to MicroPython, which should be doable. 

[James](https://www.bas.ac.uk/profile/jambyr/)

### Version History

__Restarting, please refer to [changelog](CHANGELOG)__
