A co-simulation takes a few lines of code. A `CoSimulation(start, end, step)` object is created, components are added, and inputs are subscribed to outputs. For example, `sim.subscribe("grid.P_load", "bldg*.P_facility", reduce="sum")` makes a grid meter read, over each of its steps, the summed mean power of every building whose name starts with `bldg`, whatever their step sizes. The features of @sec:method are switched on with keyword arguments: `record=["llm"]` and `trace=True` when the simulation is created, `sim.replay("llm", "runs/first")` before it runs, and `llm.make_async(delay="15min", deadline=10)` on a component. Results are pandas tables of all outputs on the master grid, plus the recorded messages with their validity and quality, the quality counts, the alignment report (how often a read had to be extrapolated) and the provenance trace. Errors inside a component stop the run with a report that names the component, the phase, the simulation time and the inputs it received, and partial results are kept.

Table: Comparison of SMMM 2.0 with representative co-simulation standards and tools, based on their documentation and publications. {#tbl:features}

| Tool | Time meaning of exchanged values | Alignment between step sizes | Record/replay | Per-value quality | Slow components | EnergyPlus |
|:---|:---|:---|:---|:---|:---|:---|
| FMI 3.0 / SSP 2.0 | continuous or held discrete values; clocks | importer's task | no | no | early return, intermediate update | via FMU export |
| HLA (IEEE 1516) | time-stamped instants | no | no (third-party tools) | no | lookahead, time-stamp order | via user federates |
| HELICS 3.6 | persistent values, one-time messages | reductions across sources at one time only | Recorder, Player and Clone apps | no (default value before first publication) | asynchronous time requests, real-time mode | via user federates |
| mosaik 3 | persistent measurements, events | value valid at the receiver's step | no | no | event-based scheduling | via adapters |
| BCVTB, EnergyPlus External Interface/FMU | none | exchange at the EnergyPlus time step | no | no | no | native |
| Sinergym, COBS, Energym, BOPTEST | none | fixed control step | no | no | no (synchronous) | native |
| Py-CoSim 2.1 (SMMM 2.0) | instant, average, amount, hold, event with validity | automatic: means, totals, hold | per component, exact | ok, clipped, fallback, late, replayed | declared decision delay and deadline | native, with internal sub-steps |
Note: Entries describe built-in features; most tools can be extended by user code. HELICS, HLA and mosaik support distributed and multi-language co-simulation, which Py-CoSim does not.
