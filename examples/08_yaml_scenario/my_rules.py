"""Plain Python functions used by scenario.yaml."""


def price_aware_thermostat(t, price=0.1, T_zone=24.0):
    """Cooling set-point [C]: pre-cool before the peak, relax during it, set up at night."""
    if not 7 <= t.hour < 22:
        return 27.5
    if 13 <= t.hour < 16:
        return 22.5
    return 26.0 if price > 0.2 else 24.0


def net_power(values):
    """Reducer for the grid meter: building load minus PV generation."""
    load, pv = values
    return (load or 0.0) - (pv or 0.0)
