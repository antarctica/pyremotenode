from setuptools import setup, find_packages

with open("requirements.txt") as fh:
    reqs = fh.read().splitlines()

setup(
    name='pyremotenode',
    packages=find_packages(),
    package_data={"": [
        "run_pyremotenode",
    ]},
    scripts=[
        "run_pyremotenode",
    ],
    include_package_data=True,
    version="0.6.0a6",
    author="James Byrne",
    author_email="digitalinnovation@bas.ac.uk",
    url='http://www.github.com/antarctica/pyremotenode',
    install_requires=reqs
)
