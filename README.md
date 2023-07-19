# Py-CoSim
EnergyPlus Python EMS-Based Co-Simulation Framework\
Author: Liang Zhang @ UArizona\
The is a work in progress. Check all the TODOs\
This is cleaned version based on `history_versions_2/example_run_clean_version_add_ML_control-mannual input_and_chatgpt_disabled_DL_occ_change_htg_to_clg_update_dir_PyCoSim.ipynb`

## Tested Versions
EnergyPlus Version: 22.2.0\
EnergyPlus API version: 0.2\
Python: 3.8.2

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

### Potential Usage
For single buildings
1. Test latest ML and control algorithms for single bulidings

For multiple buildings (community)
1. Community-scale co-simulation and operations among buildings, DER, ESS, and interdisciplianry components

### Connection with Other Components
1. Distributed Energy Resources (DER)
2. Energy Storage (Battery and Ice Storage)
3. Decision Making and Optimization Component
4. Artificial Intellegence, such as deep learning models (DL) and large language models (LLM)

### Paper
1. `Box\Liang and Alyssa Collaboration Folder\Python-EMS-Based Building Simulation Infrastructure`

### Resources
1. https://github.com/jmarrec/OpenStudio_to_EnergyPlusAPI/blob/main/OpenStudio_to_EnergyPlusAPI.ipynb
2. Py EMS API documentation
https://nrel.github.io/EnergyPlus/api/python/datatransfer.html
3. Available variable handles: EnergyPlusV22-2-0 : Input Output Reference: Page 2831

### TODO
1. function_n_data (bulding components)
- TODO: Inside function_n_data, eppy is used to identify all zone names and controllables
- TODO: use decorator
- TODO: export facility electricity, but don't know what is the variable handels.
- TODO: need a standardized timestamp. Should the timestamp be a global variable?
- TODO: used to use number of files to control timestep of two EnergyPlus models, next step should be using timestamp to sync all EnergyPlus models
2. Message Systems
- Use existing Python package 1) ZeroMQ, 2) RabbitMQ, and 3) pypubsub
3. Component Prioritazation
- Add attribute of prioritation for each component to decide the order of component run
4. Multiprocessing
- With threading, communication between running multiple instances is quite easy as you can just communicate using a messaging library like pypubsub to initiate signaling between the threads. With multiprocessing, communication is more difficult, but there are likely ways around it.
- mpi4py: "I am currently also working on a parallel programming project using the E+-API in Python.We are using mpi4py for the communication between the different instances of the E+-Buildings. We wanted to divide on core into multiple threads, and on each thread we could simulate one building at the same time. But in python there is the Global Interpreter lock, which usually is not allowing any multithreading applications.(https://realpython.com/python-gil/)"
5. TODOs in the code
6. Others
- Rhinoceros, Grasshopper, honeybee??Honeybee connects Rhino geometry and Grasshopper functionality to a selection of advanced energy modeling and simulation programs such as Radiance, Daysim, EnergyPlus, OpenStudio, and gbXML.
- Work with dompc: https://github.com/do-mpc/do-mpc
- Work with NeuroMANCER: https://github.com/pnnl/neuromancer
- In terms of parallel computing: another way to do it is to create multiple python script and run them together through bash: e.g., "python script1.py & python script2.py"
- Modualize
- hardcode year to 2009
