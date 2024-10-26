from setuptools import setup

with open("README.md", "r") as fh:
    long_description = fh.read()

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
    version = '0.5.0',
    author = 'James Byrne',
    author_email = 'jambyr@bas.ac.uk',
    url = 'http://www.github.com/antarctica/pyremotenode',
    description="A service library for controlling low power devices",
    long_description=long_description,
    long_description_content_type="text/markdown",
    install_requires=[
        # TODO: need to sort this out, comes from jimcircadian/apscheduler for python3.2 compatibility
        "apscheduler==3.0.8",
        "pyserial",
        "pytz",
        "xmodem",
        "pynmea2"
    ]
)
