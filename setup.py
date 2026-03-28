from setuptools import find_packages, setup

setup(
    name="minichain",
    version="0.1.0",
    packages=find_packages(),
    py_modules=["main"],
    install_requires=[
        "PyNaCl>=1.5.0",
        "libp2p>=0.5.0",  # Correct PyPI package name
    ],
    entry_points={
        "console_scripts": [
            "minichain=main:main",
        ],
    },
    python_requires=">=3.9",
)
