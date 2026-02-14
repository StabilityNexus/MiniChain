from setuptools import setup, find_packages

setup(
    name="minichain",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "pynacl",
        "py-libp2p",
    ],
    entry_points={
        'console_scripts': [
            'minichain=minichain.main:main',
        ],
    },
)