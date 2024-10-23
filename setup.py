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
    include_package_data=True,
    version="0.6.0a4",
    author="James Byrne",
    author_email="digitalinnovation@bas.ac.uk",
    url='http://www.github.com/antarctica/pyremotenode',
    install_requires=[
        "apscheduler",
        "pyserial",
        "pytz",
        "xmodem",
        "pynmea2"
    ]
)
