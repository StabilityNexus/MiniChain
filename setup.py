from setuptools import setup, find_packages

setup(
    name="minichain",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "pynacl>=1.5.0",
        "py-libp2p>=0.2.0",
    ],
    entry_points={
        'console_scripts': [
            'minichain=minichain.main:main',
        ],
    },
)