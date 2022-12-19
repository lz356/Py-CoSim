#!/usr/bin/env python

import setuptools
from codecs import open
import os
import sys

if sys.version_info.major < 3 or (sys.version_info.major == 3 and sys.version_info.minor < 8):
    raise Exception("This package requires python 3.8 or higher")

here = os.path.abspath(os.path.dirname(__file__))
metadata = {}

with open(os.path.join(here, '__version__.py'), 'r', encoding='utf-8') as f:
    exec(f.read(), metadata)

with open('README.md', 'r', 'utf-8') as f:
    readme = f.read()

setuptools.setup(
    name=metadata['__name__'],
    version=metadata['__version__'],
    author=metadata['__author__'],
    author_email=metadata['__author_email__'],
    description=metadata['__description__'],
    long_description=readme,
    long_description_content_type='text/markdown',
    url=metadata['__url__'],
    packages=setuptools.find_packages(),
    python_requires='>=3.8',
    install_requires=[
        'pandas',
        'scikit-learn',
        'matplotlib',
        'jupyter',
        # 'torchvision==0.13.1',
        # 'torchaudio',
        # 'torch',
    ],
    extras_require={
        'dev': [
            'pytest',
            # 'codecov',
            # 'flake8==3.8.2',
            # 'coverage',
            # 'pdoc3',
            # 'autopep8',
            # 'colorama==0.4.3'
        ]
    }
)
