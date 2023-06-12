# Rhinoceros, Grasshopper, honeybee??
# Honeybee connects Rhino geometry and Grasshopper functionality to a selection of advanced energy modeling and simulation programs such as Radiance, Daysim, EnergyPlus, OpenStudio, and gbXML.

# TODO: work with dompc
# TODO: work with NeuroMANCER: https://github.com/pnnl/neuromancer

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

# import matplotlib
# matplotlib.use('Qt5Agg')




import matplotlib.pyplot as plt
# %matplotlib qt
# %matplotlib notebook

import numpy as np
import pandas as pd

# import lime
# import shap

from threading import Thread
from multiprocessing import Process


class function_n_data:
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

        ###
        self.plot_update_interval = 10  # time steps
        ###

        ###
        self.y_outdoor = []
        self.y_zone = []
        self.y_htg = []
        self.y_clg = []
        self.x = []
        ###

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

        self.df = pd.DataFrame({'OA Temp': [], 'Zone Temp': [], 'Htg Tstat': [], 'Clg Tstat': []})


#         self.fig, (self.ax0, self.ax1) = plt.subplots(nrows=2, sharex=True, figsize=(8, 6),
#                                        gridspec_kw={'height_ratios': [2, 1]},
#                                        num='Two graphs')
#         self.h1, = self.ax0.plot([], [], label="Outdoor Air Temp")
#         self.h2, = self.ax0.plot([], [], label="Zone Temperature")
#         self.h_overshoot, = self.ax1.plot([], [], label="Overshoot")

#         self.ax0.set_ylabel('Temperature [C]')
#         self.ax0.legend(loc='lower right')
#         self.ax0.set_ylim(-25, 40)

#         self.ax1.set_title('Overshoot/undershoot compared to thermostat setpoint [C]')
#         self.ax1.set_xlabel('Zone time step index')
#         self.ax1.set_ylabel('Temperature difference [C]')
#         self.ax1.legend(loc='lower right')
#         self.ax1.set_ylim(-1.1, 1.1)
#         # plt.show(False)
#         #plt.draw()
#         self.fig.autofmt_xdate()

        # Store attributes
#         self.fig = fig
#         self.ax0 = ax0
#         self.ax1 = ax1
#         self.h1 = h1
#         self.h2 = h2
#         self.h_overshoot = h_overshoot

#         self.fig.show()
#         self.fig.canvas.draw()

    def init_plot(self):

        fig, (ax0, ax1) = plt.subplots(nrows=2, sharex=True, figsize=(8, 6),
                                       gridspec_kw={'height_ratios': [2, 1]},
                                       num='Two graphs')
        h1, = ax0.plot([], [], label="Outdoor Air Temp")
        h2, = ax0.plot([], [], label="Zone Temperature")
        h_overshoot, = ax1.plot([], [], label="Overshoot")

        ax0.set_ylabel('Temperature [C]')
        ax0.legend(loc='lower right')
        ax0.set_ylim(-25, 40)

        ax1.set_title('Overshoot/undershoot compared to thermostat setpoint [C]')
        ax1.set_xlabel('Zone time step index')
        ax1.set_ylabel('Temperature difference [C]')
        ax1.legend(loc='lower right')
        ax1.set_ylim(-1.1, 1.1)
        # plt.show(False)
        #plt.draw()
        fig.autofmt_xdate()

        # Store attributes
        self.fig = fig
        self.ax0 = ax0
        self.ax1 = ax1
        self.h1 = h1
        self.h2 = h2
        self.h_overshoot = h_overshoot

        fig.show()
        fig.canvas.draw()

    def update_line(self):


        #h1.set_data(x, y_outdoor)
        #h2.set_data(x, y_zone)

        self.h1.set_xdata(self.df.index)
        self.h1.set_ydata(self.df['OA Temp'])
        self.h2.set_xdata(self.df.index)
        self.h2.set_ydata(self.df['Zone Temp'])


        y_overshoot = []
        for i, zone_temp in enumerate(self.y_zone):
            if zone_temp < self.y_htg[i]:
                y_overshoot.append(zone_temp - self.y_htg[i])
            elif zone_temp > self.y_clg[i]:
                y_overshoot.append(zone_temp - self.y_clg[i])
            else:
                y_overshoot.append(0.0)

        self.h_overshoot.set_xdata(self.df.index)
        self.h_overshoot.set_ydata(y_overshoot)


        self.ax0.set_xlim(self.x[0], self.x[-1])
        #self.ax1.set_ylim(min(y_overshoot), max(y_overshoot))
        #ax.autoscale_view()
        self.fig.canvas.draw()
#         time.sleep(5)
        ###

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
            self.init_plot()

        if api.exchange.warmup_flag(state_argument):
            return
        self.count += 1

        # get variables
        oa_temp = api.exchange.get_variable_value(state_argument, self.oa_temp_handle)
        self.y_outdoor.append(oa_temp)

        zone_temp = api.exchange.get_variable_value(state_argument, self.zone_temp_handle)
        self.y_zone.append(zone_temp)

        zone_htg_tstat = api.exchange.get_variable_value(state_argument, self.zone_htg_tstat_handle)
        self.y_htg.append(zone_htg_tstat)

        zone_clg_tstat = api.exchange.get_variable_value(state_argument, self.zone_clg_tstat_handle)
        self.y_clg.append(zone_clg_tstat)

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

        dt = datetime.datetime(year=year, month=month, day=day, hour=hour, minute=minute)
        dt += timedelta
        self.x.append(dt)

#         TODO: Wait until find all the others have finished
#         hardcoding

#         time.sleep(0.1)

        # save varialbes
        current_df = pd.DataFrame([oa_temp, zone_temp, zone_htg_tstat, zone_clg_tstat])#,index = ['oa_temp', 'zone_temp', 'zone_htg_tstat', 'zone_clg_tstat'])
        current_df = current_df.T
        current_df.to_csv(f'dynamic_simulation_result/{self.building_id}_{self.count}.csv')

# #         if len(glob.glob(f'C:/Users/liangzhang1/Documents/GitHub/MLC_PyEMS/dynamic_simulation_result/{self.building_id}*.csv'))!= 0:
#         while len(glob.glob(f'C:/Users/liangzhang1/Documents/GitHub/MLC_PyEMS/dynamic_simulation_result/*{self.count}.csv'))!= 2:
#             time.sleep(0.01)
# #         if len(glob.glob(f'C:/Users/liangzhang1/Documents/GitHub/MLC_PyEMS/dynamic_simulation_result/*{dt}.csv'))!= 2:
# #             time.sleep(10)

# #         time.sleep(0.1)

        # controls
        api.exchange.set_actuator_value(state_argument, self.handle_clgsetp, 28)
        api.exchange.set_actuator_value(state_argument, self.handle_htgsetp, 24)

        ###
        if self.count % self.plot_update_interval == 0:

            self.df = pd.DataFrame({'OA Temp': self.y_outdoor,
                                    'Zone Temp': self.y_zone,
                                    'Htg Tstat': self.y_htg,
                                    'Clg Tstat': self.y_clg},
                                   index=self.x)
            self.update_line()
        ###

api = EnergyPlusAPI()
state = api.state_manager.new_state()

building_id = 'bldg_1'
filename_to_run = "EnergyPlus_models/RefBldgSmallOfficeNew2004_v1.4_7.2_2B_USA_AZ_PHOENIX/RefBldgSmallOfficeNew2004_v1.4_7.2_2B_USA_AZ_PHOENIX.idf"
weather_file = 'epw_weather_files/USA_CA_San.Francisco.Intl.AP.724940_TMY3.epw'
result_folder = 'results/RefBldgSmallOfficeNew2004_v1.4_7.2_2B_USA_AZ_PHOENIX/'
zone_name = "Perimeter_ZN_1"

g = function_n_data(building_id=building_id, filename_to_run=filename_to_run, weather_file=weather_file, result_folder=result_folder, zone_name=zone_name)

api.runtime.callback_begin_zone_timestep_after_init_heat_balance(state, g.callback_function)
api.runtime.run_energyplus(state,
    [
        '-w', g.weather_file,
        '-d', g.result_folder,
        g.filename_to_run
    ]
)

# If you need to call run_energyplus again, then reset the state first
api.state_manager.reset_state(state)
