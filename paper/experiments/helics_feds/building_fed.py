"""HELICS federate: the DOE small office in EnergyPlus 22.2, driven through the EnergyPlus Python API.

usage: python building_fed.py [config.json] [in.epJSON] [weather.epw] [output_dir] [log.csv]

EnergyPlus owns its time loop, so the federate lives in its callbacks. At the beginning of
every zone time step [t_k, t_k + dt) it requests HELICS time t_k, publishes the facility
power of the step that just ended, [t_k - dt, t_k), and applies the cooling set-point it
received for t_k. These are the time stamps Py-CoSim's EnergyPlus component uses.
"""
import sys
import time  # harness
from datetime import datetime
from pathlib import Path

import helics as h

sys.path.insert(0, r"C:\EnergyPlusV22-2-0")
from pyenergyplus.api import EnergyPlusAPI  # noqa: E402

HERE = Path(__file__).resolve().parent
args = sys.argv[1:] + [None] * 5
config = args[0] or str(HERE / "building.json")
model = args[1] or str(HERE.parent / "pycosim_runs" / "helics_comparison" / "in.epJSON")  # see prepare_model()
weather = args[2] or str(HERE.parents[2] / "examples" / "weather" / "USA_AZ_Tucson.Intl.AP.722740_TMY3.epw")
out_dir = args[3] or "eplus"
log_path = args[4] or "building_log.csv"
START = datetime(2023, 7, 17)

fed = h.helicsCreateValueFederateFromConfig(config)
pub_power = h.helicsFederateGetPublication(fed, "building/P_facility")
sub_setpoint = h.helicsFederateGetInputByTarget(fed, "controller/clg_sp")

api = EnergyPlusAPI()
ex = api.exchange
state = api.state_manager.new_state()
api.runtime.set_console_output_status(state, False)
handles = {}
snapshot = {}
log = []


def seconds_since_start(s):
    """Start of the zone time step that begins now (built from the step number, not current_time)."""
    minute = round((ex.zone_time_step_number(s) - 1) * 60 / ex.num_time_steps_in_hour(s))
    stamp = datetime(START.year, ex.month(s), ex.day_of_month(s), ex.hour(s), minute)
    return (stamp - START).total_seconds()


def on_begin_zone_timestep(s):
    if not ex.api_data_fully_ready(s) or ex.kind_of_sim(s) != 3:  # 3 = weather-file run period
        return
    if not handles:
        handles["P"] = ex.get_meter_handle(s, "Electricity:Facility")
        handles["sp"] = ex.get_actuator_handle(s, "Schedule:Compact", "Schedule Value", "CLGSETP_SCH")
        if min(handles.values()) < 0:
            raise RuntimeError(f"EnergyPlus handle not found: {handles}")
    if ex.warmup_flag(s):
        return
    t = seconds_since_start(s)
    if h.helicsFederateGetState(fed) == h.HELICS_STATE_STARTUP:  # first step: initial values
        h.helicsFederateEnterInitializingMode(fed)
        h.helicsPublicationPublishDouble(pub_power, snapshot["P"])
        h.helicsFederateEnterExecutingMode(fed)
    else:
        granted = h.helicsFederateRequestTime(fed, t)
        if granted != t:
            raise RuntimeError(f"granted {granted} s, expected {t} s")
        h.helicsPublicationPublishDouble(pub_power, snapshot["P"])
    setpoint = h.helicsInputGetDouble(sub_setpoint)
    ex.set_actuator_value(s, handles["sp"], setpoint)
    log.append((t, snapshot["P"], setpoint))


def on_end_zone_timestep(s):
    """Facility power [W] = meter energy of the zone step [J] / step length [s]."""
    if handles and ex.kind_of_sim(s) == 3:
        snapshot["P"] = ex.get_meter_value(s, handles["P"]) / (3600.0 / ex.num_time_steps_in_hour(s))


def guarded(callback):
    def run(s):
        try:
            callback(s)
        except Exception as exc:  # stop EnergyPlus and tell the federation instead of hanging it
            print(f"building federate error: {exc!r}", flush=True)
            api.runtime.stop_simulation(s)
            h.helicsFederateGlobalError(fed, 1, f"building: {exc!r}")
    return run


api.runtime.callback_begin_zone_timestep_after_init_heat_balance(state, guarded(on_begin_zone_timestep))
api.runtime.callback_end_zone_timestep_after_zone_reporting(state, guarded(on_end_zone_timestep))
t0 = time.perf_counter()  # harness
rc = api.runtime.run_energyplus(state, ["-w", weather, "-d", out_dir, model])
print(f"run_energyplus wall {time.perf_counter() - t0:.4f} s", flush=True)  # harness
with open(log_path, "w") as fh:
    fh.write("t_s,P_facility_W,clg_sp_C\n")
    fh.writelines(f"{t:g},{p!r},{sp!r}\n" for t, p, sp in log)
h.helicsFederateDisconnect(fed)
h.helicsFederateFree(fed)
h.helicsCloseLibrary()
sys.exit(rc)
