from setuptools import setup

setup(
    name = 'pyremotenode',
    packages = [
        'pyremotenode.tasks',
        'pyremotenode.utils',
        'pyremotenode'
    ],
    package_data = {"": [
        "run_pyremotenode",
    ]},
    scripts=[
        "run_pyremotenode",
    ],
    include_package_data = True,
    version = '0.5.0a2-lpm',
    author = 'James Byrne',
    author_email = 'jambyr@bas.ac.uk',
    url = 'http://www.github.com/antarctica/pyremotenode',
    install_requires=[
        # TODO: need to sort this out, comes from jimcircadian/apscheduler for python3.2 compatibility
        "apscheduler==3.0.8",
        "pyserial",
        "pytz",
        "xmodem",
        "pynmea2"
    ]
)
