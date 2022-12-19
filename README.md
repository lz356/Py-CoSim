# MLC-PyEMS
Author: Liang Zhang @ UArizona
Machine Learning Control Platform Based on Python EMS.
This repository is a work-in-progress.

## Tested Versions
EnergyPlus Version: 22.2.0\
EnergyPlus API version: 0.2\
Python: 3.9.12

## Installation
Download and install the latest version of [Conda](https://www.anaconda.com/products/distribution)\
Run Anaconda Prompt as Administrator\
Create a new conda environment:\
`$ conda create -n <enter-the-name-of-repository-here> python=3.8.2 pip`
`$ conda activate <enter-the-name-of-repository-here>`
Ensure that you have navigated to the top level of your cloned repository. You will execute all your pip commands from this location. For example:\
`$ cd /path/to/repository`
Install the environment needed for this repository:\
`$ pip install -e .[dev]`

## Referneces
EnergyPlus API Example and Tutorial: https://github.com/jmarrec/OpenStudio_to_EnergyPlusAPI/blob/main/OpenStudio_to_EnergyPlusAPI.ipynb
