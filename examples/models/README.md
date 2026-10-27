# Example building models and weather

| File | Source | Notes |
|---|---|---|
| `small_office.idf` | U.S. DOE commercial reference building *Small Office, new construction 2004* (v1.4_7.2, climate zone 2B, Phoenix AZ), EnergyPlus 22.2 | One storey, 511 m², 5 conditioned zones + attic, packaged single-zone CAV rooftop units with DX cooling and gas furnace. Used in examples 02–05 and 07. |
| `apartment_midrise.idf` | `ASHRAE901_ApartmentMidRise_STD2019_Denver.idf` from the EnergyPlus 22.2 `ExampleFiles` (PNNL ASHRAE 90.1-2019 prototype) | Four storeys with 23 dwelling units + office; one split system per unit. Example 06 sizes it for the simulated climate with `sizing="weather_file"`. |
| `../weather/USA_AZ_Tucson.Intl.AP.722740_TMY3.epw` | TMY3 typical meteorological year, Tucson International Airport | Arizona does not observe daylight-saving time, so the examples pass `daylight_saving=False`. |

The DOE reference/prototype models are distributed with EnergyPlus under its BSD-style license. Run
`pycosim handles <model> -w <epw>` to see every sensor, meter and actuator a model offers.
