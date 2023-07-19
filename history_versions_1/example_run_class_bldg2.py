# Rhinoceros, Grasshopper, honeybee??
# Honeybee connects Rhino geometry and Grasshopper functionality to a selection of advanced energy modeling and simulation programs such as Radiance, Daysim, EnergyPlus, OpenStudio, and gbXML.

# dompc

# TODO: need callback_function_2, may need decorator

# TODO: another way to do it is to create multiple python script and run them together through bash
# python script1.py & python script2.py &

# TODO: multiprocessing
# With threading, communication between running multiple instances is quite easy as you can just communicate using a messaging
# library like pypubsub to initiate signaling between the threads. With multiprocessing, communication is more difficult,
# but there are likely ways around it.

# TODO: mpi4py
# I am currently also working on a parallel programming project using the E+-API in Python.
# We are using mpi4py for the communication between the different instances of the E+-Buildings.
# We wanted to divide on core into multiple threads, and on each thread we could simulate one building at the same time.
# But in python there is the Global Interpreter lock, which usually is not allowing any multithreading applications.
# (https://realpython.com/python-gil/)
# Is it really working with multiprocessing (core with multiple threads) and is it also working in combination with mpi4py?

# required packages: ['pandas','sklearn','matplotlib','jupyter','lime', 'honeybee_energy']

import sys
sys.path.insert(0, 'C:/EnergyPlusV22-2-0/')
from pyenergyplus.api import EnergyPlusAPI

import pyenergyplus

import os
import glob
import time
import shutil
import sys
import datetime

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import lime
import shap

from threading import Thread
from multiprocessing import Process

# constants and parameters
dynamic_electricity_price = [1,1,1,1,1,1]
dynamic_weather = []
dynamic_battery = []
dynamic_PV = []
dynamic_occupancy = []
transportation_data = []
# realize cosimulation among different buildings
# - VOLTTRON?
# - Rhinoceros Grasshopper??

class funcation_n_data_2:
    def __init__(self, building_id, filename_to_run, weather_file, result_folder, zone_name):
        self.building_id = building_id
        self.filename_to_run = filename_to_run
        self.weather_file = weather_file
        self.result_folder = result_folder
        self.zone_name = zone_name

        self.got_handles = False
        self.oa_temp_handle = -1
        self.zone_temp_handle = -1
        self.zone_htg_tstat_handle = -1
        self.zone_clg_tstat_handle = -1
        self.count = 0

#         self.y_outdoor = []
#         self.y_zone = []
#         self.y_htg = []
#         self.y_clg = []
#         self.x = []

        self.variables_df = pd.DataFrame([])
        self.controls_df = pd.DataFrame([])

        self.years = []
        self.months = []
        self.days = []
        self.hours = []
        self.minutes = []
        self.current_times = []
        self.actual_date_times = []
        self.actual_times = []

    def callback_function(self, state_argument):

        if not self.got_handles:
            if not api.exchange.api_data_fully_ready(state_argument):
                return

            # Variable handles
            self.oa_temp_handle = api.exchange.get_variable_handle(state_argument, u"SITE OUTDOOR AIR DRYBULB TEMPERATURE", u"ENVIRONMENT")
            self.zone_temp_handle = api.exchange.get_variable_handle(state_argument, "Zone Mean Air Temperature", self.zone_name)
            self.zone_htg_tstat_handle = api.exchange.get_variable_handle(state_argument, "Zone Thermostat Heating Setpoint Temperature", self.zone_name)
            self.zone_clg_tstat_handle = api.exchange.get_variable_handle(state_argument, "Zone Thermostat Cooling Setpoint Temperature", self.zone_name)

            # Actuarator handles
            self.handle_clgsetp = api.exchange.get_actuator_handle(state_argument, "Schedule:Compact", "Schedule Value", "CLGSETP_SCH")
            self.handle_htgsetp = api.exchange.get_actuator_handle(state_argument, "Schedule:Compact", "Schedule Value", "HTGSETP_SCH")

            if -1 in [self.oa_temp_handle, self.zone_temp_handle, self.zone_htg_tstat_handle, self.zone_clg_tstat_handle, self.handle_clgsetp,  self.handle_htgsetp]:
                print("***Invalid handles, check spelling and sensor/actuator availability")
                sys.exit(1)
            self.got_handles = True

        if api.exchange.warmup_flag(state_argument):
            return
        self.count += 1

        # Skip warmup
        if api.exchange.warmup_flag(state_argument):
            return

        # get variables
        oa_temp = api.exchange.get_variable_value(state_argument, self.oa_temp_handle)
#         self.y_outdoor.append(oa_temp)

        zone_temp = api.exchange.get_variable_value(state_argument, self.zone_temp_handle)
#         self.y_zone.append(zone_temp)

        zone_htg_tstat = api.exchange.get_variable_value(state_argument, self.zone_htg_tstat_handle)
#         self.y_htg.append(zone_htg_tstat)

        zone_clg_tstat = api.exchange.get_variable_value(state_argument, self.zone_clg_tstat_handle)
#         self.y_clg.append(zone_clg_tstat)

        # get information/data from PV, energy storage, weather, electricity price, and other buildings
        # TODO

        # Time
        year = api.exchange.year(state)
        month = api.exchange.month(state)
        day = api.exchange.day_of_month(state)
        hour = api.exchange.hour(state)
        minute = api.exchange.minutes(state)
        current_time = api.exchange.current_time(state)
        actual_date_time = api.exchange.actual_date_time(state)
        actual_time = api.exchange.actual_time(state)

        # Year is bogus, seems to be reading the weather file year instead...
        # So harcode it to 2009
        year = 2009
        self.years.append(year)
        self.months.append(month)
        self.days.append(day)
        self.hours.append(hour)
        self.minutes.append(minute)

        self.current_times.append(current_time)
        self.actual_date_times.append(actual_date_time)
        self.actual_times.append(actual_time)

        timedelta = datetime.timedelta()
        if hour >= 24.0:
            hour = 23.0
            timedelta += datetime.timedelta(hours=1)
        if minute >= 60.0:
            minute = 59
            timedelta += datetime.timedelta(minutes=1)

        dt = datetime.datetime(year=year, month=month, day=day, hour=hour, minute=minute).strftime("%m-%d-%Y %H-%M")
#         dt += timedelta
#         self.x.append(dt)

# #         TODO: Wait until find all the others have finished
# #         hardcoding
#         if len(glob.glob(f'C:/Users/liangzhang1/Documents/GitHub/MLC_PyEMS/dynamic_simulation_result/*.csv'))!= 0:
#             while len(glob.glob(f'C:/Users/liangzhang1/Documents/GitHub/MLC_PyEMS/dynamic_simulation_result/*{dt}.csv'))!= 1:
#                 time.sleep(2)
#     #         if len(glob.glob(f'C:/Users/liangzhang1/Documents/GitHub/MLC_PyEMS/dynamic_simulation_result/*{dt}.csv'))!= 2:
#     #             time.sleep(10)

#         # save varialbes
#         current_df = pd.DataFrame([oa_temp, zone_temp, zone_htg_tstat, zone_clg_tstat])#,index = ['oa_temp', 'zone_temp', 'zone_htg_tstat', 'zone_clg_tstat'])
#         current_df = current_df.T
#         current_df.to_csv(f'dynamic_simulation_result/{self.building_id}_{dt}.csv')

        time.sleep(0.01)

        # controls
        api.exchange.set_actuator_value(state_argument, self.handle_clgsetp, 28)
        api.exchange.set_actuator_value(state_argument, self.handle_htgsetp, 24)

api_2 = EnergyPlusAPI()
state_2 = api_2.state_manager.new_state()

filename_to_run = "EnergyPlus_models/RefBldgSmallOfficeNew2004_v1.4_7.2_2B_USA_AZ_PHOENIX/RefBldgSmallOfficeNew2004_v1.4_7.2_2B_USA_AZ_PHOENIX_2.idf"
weather_file = 'epw_weather_files/USA_CA_San.Francisco.Intl.AP.724940_TMY3_2.epw'
result_folder = 'results/RefBldgSmallOfficeNew2004_v1.4_7.2_2B_USA_AZ_PHOENIX_2/'
zone_name = "Perimeter_ZN_1"

g_2 = funcation_n_data_2(building_id='bldg_2', filename_to_run=filename_to_run, weather_file=weather_file, result_folder=result_folder, zone_name=zone_name)

api_2.runtime.callback_begin_zone_timestep_after_init_heat_balance(state_2, g_2.callback_function)
api_2.runtime.run_energyplus(state_2,
    [
        '-w', g_2.weather_file,
        '-d', g_2.result_folder,
        g_2.filename_to_run
    ]
)

# If you need to call run_energyplus again, then reset the state first
api_2.state_manager.reset_state(state_2)
