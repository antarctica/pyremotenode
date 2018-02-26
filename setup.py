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
    version = '0.2.44',
    author = 'James Byrne',
    author_email = 'zdm@bas.ac.uk',
    url = 'http://www.antarctica.ac.uk',
    install_requires=[
        "apscheduler",
        "pyserial"
    ]
)
