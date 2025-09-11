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
    version="0.6.1a0",
    author="James Byrne",
    author_email="digitalinnovation@bas.ac.uk",
    description="A service library for controlling low power devices",
    long_description=long_description,
    long_description_content_type="text/markdown",
    keywords="telecommunications, scheduling",
    python_requires='>=3.5, <3.10',
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Information Technology",
        "Intended Audience :: Science/Research",
        "Intended Audience :: Telecommunications Industry ",
        "License :: OSI Approved :: MIT License",
        "Natural Language :: English",
        "Operating System :: POSIX",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Topic :: Communications",
        "Topic :: Communications :: Telephony",
        "Topic :: Scientific/Engineering",
        "Topic :: Software Development :: Embedded Systems",
    ],
    extras_require={
        'docs': [],
        'test': ['pytest>3,<6']
    },
    test_suite='tests',
    url='http://www.github.com/antarctica/pyremotenode',
    install_requires=reqs
)
