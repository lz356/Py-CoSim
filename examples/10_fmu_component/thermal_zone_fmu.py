"""The demo FMU of example 10: a single-zone house as a first-order (1R1C) thermal model.

    C dT/dt = UA (T_out - T) + A_sol GHI + Q_hvac

This file is the *source* of a co-simulation FMU (FMI 2.0) built with pythonfmu
(``pip install pythonfmu``)::

    pythonfmu build -f thermal_zone_fmu.py -d output

``run.py`` builds it automatically. The variable names (``TOut``, ``HGloHor``,
``QHVAC``, ``TZon``, ``QEnv``) imitate those of a Modelica model, so the example
can show how FMU variables are renamed to Py-CoSim ports. Any co-simulation
FMU exported from Modelica (Dymola, OpenModelica), Simulink, ... is used the
same way.
"""
import math

from pythonfmu import Fmi2Causality, Fmi2Slave, Fmi2Variability, Real


class ThermalZone(Fmi2Slave):
    author = "Py-CoSim example 10"
    description = "Single-zone house, first-order thermal model: C dT/dt = UA (T_out - T) + A_sol GHI + Q_hvac"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # parameters (defaults for a ~150 m2 house)
        self.C = 2.5e7        # heat capacity of air, furniture and the effective building mass [J/K]
        self.UA = 165.0       # envelope + infiltration conductance [W/K]
        self.A_sol = 6.0      # effective solar aperture [m2]
        self.T_start = 26.0   # initial zone temperature [C]
        # inputs
        self.TOut = 30.0      # outdoor dry-bulb temperature [C]
        self.HGloHor = 0.0    # global horizontal irradiance [W/m2]
        self.QHVAC = 0.0      # heat added by the HVAC system [W] (negative = cooling)
        # outputs
        self.TZon = self.T_start  # zone temperature at the communication point [C]
        self.QEnv = 0.0           # mean heat gain through the envelope over the last step [W]

        fixed = dict(causality=Fmi2Causality.parameter, variability=Fmi2Variability.fixed)
        for name in ("C", "UA", "A_sol", "T_start"):
            self.register_variable(Real(name, **fixed))
        for name in ("TOut", "HGloHor", "QHVAC"):
            self.register_variable(Real(name, causality=Fmi2Causality.input))
        for name in ("TZon", "QEnv"):
            self.register_variable(Real(name, causality=Fmi2Causality.output))

    def exit_initialization_mode(self):
        self.TZon = self.T_start  # parameters are set by now

    def do_step(self, current_time, step_size):
        # exact solution for inputs held constant over the communication step
        q = self.A_sol * max(self.HGloHor, 0.0) + self.QHVAC
        T_eq = self.TOut + q / self.UA
        T_new = T_eq + (self.TZon - T_eq) * math.exp(-step_size * self.UA / self.C)
        self.QEnv = self.C * (T_new - self.TZon) / step_size - q
        self.TZon = T_new
        return True
