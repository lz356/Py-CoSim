# AI in the loop: LLMs and reinforcement learning

Py-CoSim treats AI decision makers as ordinary components. A large language model (LLM)
becomes an `LLMSupervisor` that reads measurements and writes set-points; a reinforcement
learning (RL) agent becomes an `ExternalAgent` that a training library drives through the
standard Gymnasium interface. Buildings, PV, tariffs and everything else stay unchanged.

## LLM-in-the-loop control

### How it works

Every `step_size` (default one hour) the `LLMSupervisor`

1. builds a prompt from the operator's **intent** and the current **observations**,
2. sends it to a **backend** (Claude, an OpenAI-compatible server, a local Ollama model, or an
   offline stand-in),
3. passes the answer through **guardrails** and publishes the resulting set-points, which the
   building then follows until the next decision (a multi-rate co-simulation).

The prompt has a fixed system part (role, the allowed keys and ranges, "respond with one
JSON object") and a user part like

```text
OPERATOR INTENT: Minimise electricity cost, especially during the evening price peak, ...

STATE: {"time": "2023-07-17 16:00", "weekday": "Monday",
        "observations": {"T_zone": 24.12, "T_out": 39.4, "price": 0.3, "P_elec": 41.87},
        "observation_meaning": {"T_zone": "core zone air temperature [C]", ...},
        "current_setpoints": {"clg_sp": 24.0, "htg_sp": 20.0}, "occupied": true}
```

The expected answer is `{"clg_sp": 26.0, "htg_sp": 20.0, "reason": "..."}`.

### A complete example

```python
import pycosim as pc
from pycosim.components import AnthropicBackend, LLMSupervisor, TimeOfUseTariff

EPW = "examples/weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw"
sim = pc.CoSimulation("2023-07-17", "2023-07-20", "15min", name="llm_office")
sim.add(pc.EnergyPlusComponent(
    "office", "examples/models/small_office.idf", EPW,
    sensors={"T_zone": ("Zone Mean Air Temperature", "Core_ZN"),
             "T_out": ("Site Outdoor Air Drybulb Temperature", "Environment")},
    meters={"P_elec": ("Electricity:Facility", "kW")},
    actuators={"clg_sp": ("Schedule:Compact", "Schedule Value", "CLGSETP_SCH"),
               "htg_sp": ("Schedule:Compact", "Schedule Value", "HTGSETP_SCH")},
    daylight_saving=False))
sim.add(TimeOfUseTariff("tariff", peak_hours=(16, 21), peak_price=0.30, offpeak_price=0.08))
sim.add(LLMSupervisor(
    "llm", AnthropicBackend(),                     # model="claude-opus-5-5" by default
    intent="Minimise electricity cost during the evening peak; keep occupants reasonably comfortable.",
    setpoints={"clg_sp": (21.0, 30.0, 24.0), "htg_sp": (15.0, 23.0, 20.0)},
    observations={"T_zone": "core zone air temperature [C]", "T_out": "outdoor air temperature [C]",
                  "price": "electricity price [$/kWh]", "P_elec": "building electric power [kW]"},
    step_size="1h", max_change=3.0, timeout=60.0, occupied_hours=(7, 19)))
for port in ("T_zone", "T_out", "P_elec"):
    sim.connect(f"office.{port}", f"llm.{port}")
sim.connect("tariff.price", "llm.price")
sim.connect("llm.clg_sp", "office.clg_sp")
sim.connect("llm.htg_sp", "office.htg_sp")
res = sim.run(progress=True)
```

[Example 05](examples.md#05--llm-supervisor) is the runnable version with a `--backend`
switch.

### Backends and credentials

| Backend | Use | Credentials |
|---|---|---|
| `AnthropicBackend(model="claude-opus-5-5", max_tokens=16000, effort=None, server_fallback=True, client=None)` | Claude through the official `anthropic` Python SDK (`pip install anthropic`) | `ANTHROPIC_API_KEY` environment variable, or a profile created with `ant auth login` (resolved by the SDK) |
| `OpenAIBackend(model, base_url="https://api.openai.com/v1", api_key=None, timeout=60.0, extra=None)` | OpenAI, or any OpenAI-compatible server (vLLM, LM Studio, …) via `base_url` | `OPENAI_API_KEY` or `api_key=` |
| `OllamaBackend(model="llama3.1", host="http://localhost:11434", timeout=120.0)` | open-weight models on your own machine (`ollama serve`) | none |
| `OfflineStandInBackend()` | **not an LLM** — keyword rules for tests/demos ([details](component-library.md#backends)) | none |

`AnthropicBackend` options:

* `model` — any Claude model id; the default is `claude-opus-5-5`.
* `effort` — `"low"`, `"medium"`, `"high"`, `"xhigh"` or `"max"`; trades thoroughness
  against latency and cost (sent as `output_config={"effort": ...}`). `None` uses the API
  default. A short set-point decision often does not need high effort — compare a few
  settings on your case.
* `server_fallback=True` — if Claude declines a request, the API re-runs it on a fallback
  model (a beta API feature); a refusal that is not rescued is treated as a fallback by the
  guardrails. The model that actually answered is recorded in the event log (`model`
  column), so you can see when a fallback model was used.
* `max_tokens` — upper limit for the answer (default 16000); the model's internal reasoning
  counts towards it. If an answer is cut off at this limit, the backend raises an error
  ("response truncated at max_tokens=…; increase max_tokens") and the guardrails fall back,
  instead of using a truncated answer.
* `client` — pass your own `anthropic.Anthropic(...)` client, e.g. with a different timeout
  or retry policy.

Never write API keys in scripts or notebooks (Py-CoSim 0.x had one in its source). Set them
in the environment of the terminal that runs Python:

```powershell
$env:ANTHROPIC_API_KEY = "..."      # Windows PowerShell, current session
```

```bash
export ANTHROPIC_API_KEY="..."      # macOS / Linux
```

Your own backend only needs a `complete(system, prompt) -> str` method:

```python
from pycosim.components import LLMBackend

class MyBackend(LLMBackend):
    name = "my-backend"
    def complete(self, system: str, prompt: str) -> str:
        return call_my_model(system, prompt)        # must return the model's text answer
```

### Guardrails: why the building stays safe

LLM output is untrusted. Every answer goes through these steps:

| Step | What happens | Recorded as |
|---|---|---|
| parse | take the first JSON object in the text | fallback "no JSON object in response" |
| validate | keep only declared set-points with finite numbers; ignore other keys | `ignored_keys`, `missing_or_invalid` |
| bound | clip to `[min, max]` | `clipped` |
| rate-limit | limit the change per decision to `max_change` | `rate_limited` |
| fallback | on timeout, refusal, network error, truncated answer or no usable value: hold the last valid set-points (initially the defaults) | `fallback` with the reason |

A call that exceeds `timeout` is abandoned: the supervisor stops waiting for it, and the
next decision starts a fresh call instead of queueing behind the hung one.

The output `valid` is 1 when the LLM answer was used, 0 on fallback; `latency` is the
response time in seconds. Test the guardrails with `FaultInjectionBackend`, which injects
malformed text, out-of-range values (+40 °C), hallucinated keys, refusals and timeouts:

```python
from pycosim.components import FaultInjectionBackend, OfflineStandInBackend

backend = FaultInjectionBackend(OfflineStandInBackend(), p_malformed=0.1, p_out_of_range=0.1,
                                p_extra_keys=0.05, p_refusal=0.05, p_timeout=0.05, seed=1)
```

### Auditability: the event log

Every decision is written to the run's event log with `kind == "llm"`: the backend, the
model that answered (`model`; filled in when the supervisor uses an `AnthropicBackend`
directly — currently empty for other backends and when it is wrapped in
`RecordingBackend`/`FaultInjectionBackend`), the full prompt, the raw response, any error, the guardrail notes, the
applied set-points and the latency.

```python
llm_log = res.events[res.events.kind == "llm"]
print(llm_log[["time", "model", "applied", "guard", "latency_s"]].head())
print(llm_log.iloc[0]["prompt"])
print(llm_log.iloc[0]["response"])
print(res.meta["component_metadata"]["llm"])   # backend, intent, calls, valid, fallback, clipped, rate_limited
```

The same table is saved as `events.csv` next to the time series, so an LLM-in-the-loop
experiment can be reviewed decision by decision afterwards.

### Cost and latency

* **Number of calls** = simulated hours ÷ decision interval × number of supervisors. One
  building with hourly decisions for one week is 168 calls; ten buildings for a summer
  (92 days) is about 22,000 calls.
* **Tokens per call**: the prompt is a few hundred tokens and grows with the number of
  observations; the answer is a short JSON object, plus any reasoning tokens the model
  produces. At the time of writing, `claude-opus-5-5` costs $4 per million input tokens and
  $20 per million output tokens — check the provider's current price list and multiply out
  before a large study.
* **Latency**: each call usually takes seconds. The co-simulation waits for the answer (up to
  `timeout`), and supervisors are called one after the other, so latency adds directly to
  the wall time: 168 calls × 3 s ≈ 8 minutes.
* Ways to save: use a longer `step_size`; develop with `OfflineStandInBackend`; record a run
  once and replay it for analysis and plots; run a local model with `OllamaBackend`.

### Reproducibility: record and replay

LLM answers vary between calls. To make an experiment exactly repeatable, record it once and
replay it:

```python
from pycosim.components import AnthropicBackend, RecordingBackend, ReplayBackend

backend = RecordingBackend(AnthropicBackend(), "llm_log.jsonl")   # first run: real calls, saved
backend = ReplayBackend("llm_log.jsonl")                          # later runs: no API calls
```

The JSON-lines file stores, for each call, a hash of the system prompt and prompt, the
backend description, and either the response or — if the call failed (timeout, refusal,
network error, injected fault) — the error. During replay, recorded errors are raised again
as `ReplayedError`, so the replayed run reproduces the same fallbacks as the original.
`ReplayBackend` answers a prompt only if exactly the same prompt was recorded, so the
scenario (time window, seed, components, observations, `decimals`) must be unchanged;
otherwise it raises `KeyError` (and the supervisor falls back). With `strict=False`, unknown
prompts get the answer `"{}"` instead. Keep the `.jsonl` file with your results — it is the
evidence of what the model said.

---

## Reinforcement learning

`pycosim.gym` turns any co-simulation into a [Gymnasium](https://gymnasium.farama.org)
environment (`pip install -e ".[rl]"`), so you can use Stable-Baselines3, RLlib, CleanRL or
your own algorithm.

### 1. Put an `ExternalAgent` where the policy acts

```python
ExternalAgent(name, observations, actions, step_size=None, policy=None)
```

* `observations` — list of input names (or `{name: default}`); connect measurements to them;
* `actions` — `{output_name: (low, high)}`; actions are clipped to these bounds and the
  initial output is the middle of the range;
* `step_size` — decision interval (e.g. `"1h"`);
* `policy` — optional `policy(t, observation_dict) -> action` used when no RL library drives
  the agent (see below). Without a policy and outside training, the agent holds its last
  action.

```python
import numpy as np
import pycosim as pc
from pycosim.components import EPWWeather, RCBuilding, TimeOfUseTariff
from pycosim.gym import CoSimEnv, ExternalAgent

EPW = "examples/weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw"
OBS = ["T_zone", "price"]

def make_sim():
    sim = pc.CoSimulation("2023-07-17", "2023-07-20", "15min", save=False, log_level="WARNING")
    sim.add(EPWWeather("weather", EPW), RCBuilding("house", floor_area=150),
            TimeOfUseTariff("tariff"),
            ExternalAgent("agent", observations=OBS, actions={"clg_sp": (22.0, 28.0)},
                          step_size="1h"))
    sim.connect("weather.T_out", "house.T_out")
    sim.connect("weather.GHI", "house.GHI")
    sim.connect("house.T_zone", "agent.T_zone")
    sim.connect("tariff.price", "agent.price")
    sim.connect("agent.clg_sp", "house.clg_sp")
    return sim
```

`make_sim` must return a **new, not yet started** simulation each time it is called: the
environment calls it at every `reset()`. Use `save=False` so episodes do not write result
folders.

### 2. Wrap it in `CoSimEnv`

```python
def reward(obs, sim):
    """obs: the agent's observations now; sim: the running CoSimulation."""
    discomfort = max(obs["T_zone"] - 26.0, 0.0) if 8 <= sim.now.hour < 22 else 0.0
    return -obs["price"] * sim.bus.latest("house.P_elec") / 1000 - 0.5 * discomfort

env = CoSimEnv(make_sim, agent="agent", reward=reward,
               observation_bounds={"T_zone": (10.0, 45.0), "price": (0.0, 1.0)})
```

* `reward(obs_dict, sim)` is evaluated at each decision point for the action taken at the
  previous one: `obs_dict` holds the agent's observations *after* the action has acted for
  one decision interval, and `sim` gives access to everything else (`sim.now`,
  `sim.bus.latest("comp.port")`, …). On the final `step()` (the one that returns
  `terminated=True`) there is no new decision point, so `obs_dict` repeats the last
  observation.
* `action_space` is a `Box` built from the action bounds; `observation_space` a `Box` with
  `observation_bounds` (default unbounded). Observations are float32 vectors in the order of
  `observations`; a missing value becomes NaN — connect every observation.
* `reset(seed=...)` builds a new simulation (a given seed replaces the simulation seed) and
  returns `(observation, {"time": ...})`; `step(action)` returns
  `(observation, reward, terminated, truncated, info)`; `terminated` is `True` at the end of
  the simulation window, after which `env.results` holds the episode's `Results`.
* The environment passes Gymnasium's `check_env`.

A hand-written loop:

```python
obs, info = env.reset(seed=0)
done, total = False, 0.0
while not done:
    action = np.array([24.0 if obs[1] < 0.2 else 26.0], dtype=np.float32)
    obs, r, done, truncated, info = env.step(action)
    total += r
print(total)
```

### 3. Train with Stable-Baselines3 (or any Gymnasium library)

```python
from stable_baselines3 import PPO          # pip install stable-baselines3

model = PPO("MlpPolicy", env, verbose=1)
model.learn(total_timesteps=20_000)
model.save("ppo_thermostat")
```

[Example 07](examples.md#07--reinforcement-learning) trains a small linear policy with the
cross-entropy method instead, to stay dependency-free.

### 4. Run the trained policy inside a normal co-simulation

Give the agent a `policy` and run the scenario like any other — with results saved, plots,
other components, EnergyPlus, and no RL library in the loop:

```python
sim = make_sim()
sim.save, sim.name = True, "ppo_evaluation"

def policy(t, obs):
    x = np.array([obs[k] for k in OBS], dtype=np.float32)
    action, _ = model.predict(x, deterministic=True)
    return action                                   # array, or a dict {"clg_sp": ...}

sim["agent"].policy = policy                        # or ExternalAgent(..., policy=policy)
res = sim.run()
```

### Tips

* Prototype with `RCBuilding` (milliseconds per episode); switch to `EnergyPlusComponent`
  once the set-up works. With EnergyPlus, every `reset()` starts a new EnergyPlus run
  (sizing and warm-up take a few seconds), so prefer longer episodes and fewer resets.
* Normalise observations and rewards to order 1 (e.g. power in kW, not W) — RL algorithms
  learn much better.
* Several environments in parallel (e.g. Stable-Baselines3 `SubprocVecEnv`) each build their
  own simulation; EnergyPlus workers are separate processes anyway.
* Gymnasium's checker warns that the action space is not normalised to [-1, 1]; that is
  only a recommendation. Rescale inside the policy if your algorithm needs it.
