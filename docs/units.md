# Unit contracts: EPANET vs SWMM

Callers send `feature_settings`, `options_settings`, and `other_settings` in
**INP file units** — the same values that appear in the EPANET or SWMM INP —
**not** SI.

| Engine | Library | In-memory model | On `update_inp_from_settings` |
|--------|---------|-----------------|-------------------------------|
| EPANET | WNTR | SI | Convert INP → SI with `wntr.epanet.util.to_si` / `HydParam` |
| SWMM | swmm-api | INP units (`FLOW_UNITS`) | **No conversion** — write values as-is |

EPANET result export (`bin_handler`) already converts SI → INP with
`from_si` for BIN → JSON/DB. That path is unchanged.

Implementation: `hydraulic_engine.epanet.units` and
`EpanetInpHandler.update_inp_from_settings`.

## Apply order (EPANET)

When options and features are passed in the same call, **options are applied
first**, then features, then other settings. That way `inpfile_units` and
`headloss` from the call govern feature conversion (e.g. Darcy-Weisbach
roughness). Within options, `inpfile_units` is applied before pressure fields
so pressure uses the updated unit system.

## EPANET: always convert (when set)

| Settings field | `HydParam` | Notes |
|----------------|------------|-------|
| `elevation` | Elevation | Junctions / tanks / nodes |
| `demand_list[].base_demand` | Demand | Via `add_demand` |
| `emitter_coefficient` | EmitterCoeff | |
| `base_head` | HydraulicHead | Reservoirs |
| `init_level`, `min_level`, `max_level` | HydraulicHead | Same length factor as WNTR Length |
| tank `diameter` | TankDiameter | |
| `min_vol` | Volume | |
| pipe `length` | Length | |
| pipe / valve `diameter` | PipeDiameter | mm→m metric; in→m US |
| pump `power` | Power | kW→W metric; hp→W US |
| `minimum_pressure`, `required_pressure` | Pressure | `options.hydraulic` |

## EPANET: convert only depending on another field

### Pipe `roughness`

- If `options.hydraulic.headloss == "D-W"`: convert with
  `HydParam.RoughnessCoeff` and `darcy_weisbach=True` (metric mm→m; US
  1e-3 ft→m).
- If H-W or C-M: **do not convert** (Hazen-Williams / Chezy-Manning C-factor).

### Valve `initial_setting`

Resolve `valve_type` from the settings object first, else the existing WNTR
valve:

| Valve type | Conversion |
|------------|------------|
| PRV / PSV / PBV | `HydParam.Pressure` |
| FCV | `HydParam.Flow` |
| TCV | None (dimensionless loss coefficient) |
| GPV | None (setting is a curve name; non-numeric left as-is) |

Pump `initial_setting` / `base_speed` are relative speed — **never** convert.

### Curve `points`

Resolve `curve_type` from settings else the existing curve (`PUMP` treated as
`HEAD`):

| Curve type | X | Y |
|------------|---|---|
| VOLUME | Length | Volume |
| HEAD / HEADLOSS | Flow | HydraulicHead |
| EFFICIENCY | Flow | Unchanged (percent) |
| Unknown | No conversion | No conversion |

## EPANET: controls and rules

`EpanetControl.text` and `EpanetRule.text` are **EPANET INP syntax** in the
network ``inpfile_units`` (same as ``[CONTROLS]`` / ``[RULES]`` in the file).

On apply, WNTR parsers (`_read_control_line`, `_EpanetRule.parse_rules_lines`)
convert numeric thresholds to SI by attribute (pressure, level, flow, valve
setting type, etc.). Time-based clauses (`AT TIME`, `OPEN`/`CLOSED`) are not
converted. Invalid INP syntax fails at WNTR parse time.

## EPANET: never convert

`initial_status`, `cv`, `valve_type`, tags / names, `overflow`, `base_speed`,
pump `initial_setting`, `minor_loss`, pattern multipliers, dimensionless
options (`specific_gravity`, `viscosity`, exponents, multipliers), times
(already seconds), coordinates / vertices.

`valve_type` on settings is used only to resolve `initial_setting` units; it
is not written onto the WNTR valve (type is fixed by the link subclass).

## EPANET: deferred (do not guess)

These need extra context in WNTR and are **not** converted in this release:

- `initial_quality` (depends on quality mode / `QualParam`)
- pipe / tank `bulk_coeff` / `wall_coeff` (depend on reaction order)
- `energy_price` / `global_price` (WNTR energy I/O uses an inverted
  `from_si` / `to_si` pairing vs other parameters)

## Reading demands (EPANET)

`EpanetInpHandler.get_demands()` returns junction demands in **INP units**:

```python
{
  "units": "LPS",  # from inpfile_units
  "junctions": {
    "N1": {
      "demand_list": [
        {"base_demand": ..., "pattern_name": ..., "category": ...}
      ]
    }
  }
}
```

## SWMM

`SwmmInpHandler.update_inp_from_settings` writes feature and option values
directly onto swmm-api section objects. Units follow the file’s `FLOW_UNITS`
(and related INP conventions). Do **not** introduce WNTR-style SI conversion
on the SWMM path.

## Adding new fields

When exposing a new numeric settings field, check WNTR `epanet/io.py` for the
matching `to_si` / `HydParam` (and any dependency on headloss, valve type, or
curve type). Update `epanet/units.py` and this document together — do not add
blind `setattr` of INP-unit values onto WNTR objects.
