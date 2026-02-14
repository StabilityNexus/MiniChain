from setuptools import setup, find_packages

setup(
    name="minichain",
    version="0.1.0",
    packages=find_packages(),  # Will detect core, consensus, network, etc.
    py_modules=["main"],
    install_requires=[
        "pynacl",
        "py-libp2p",
    ],
    entry_points={
        "console_scripts": [
            "minichain=main:main",  # Points to main.py -> main()
        ],
    },
    python_requires=">=3.8",
)
