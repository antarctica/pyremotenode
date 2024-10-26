from setuptools import setup, find_packages

with open("requirements.txt") as fh:
    reqs = fh.read().splitlines()

with open("README.md") as fh:
    long_description = fh.read()

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
    version="0.6.0",
    author="James Byrne",
    author_email="digitalinnovation@bas.ac.uk",
    description="A service library for controlling low power devices",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url='http://www.github.com/antarctica/pyremotenode',
    install_requires=reqs
)
