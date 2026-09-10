# Hydraulic Engine

| | |
| --- | --- |
| Testing | [![CI - Test](https://github.com/bgeo-gis/hydraulic-engine/actions/workflows/test.yml/badge.svg)](https://github.com/bgeo-gis/hydraulic-engine/actions/workflows/test.yml) |
| Package | [![PyPI Latest Release](https://img.shields.io/pypi/v/hydraulic-engine.svg)](https://pypi.org/project/hydraulic-engine/) [![PyPI Downloads](https://img.shields.io/pypi/dm/hydraulic-engine.svg?label=PyPI%20downloads)](https://pypi.org/project/hydraulic-engine/) |
| Meta | [![License - GNU GPL3](https://img.shields.io/pypi/l/hydraulic-engine.svg)](https://github.com/bgeo-gis/hydraulic-engine/blob/main/LICENSE) |

Python toolkit to run **SWMM** and **EPANET** simulations, work with their input and result files, and export results to a Giswater PostgreSQL database or a FROST SensorThings endpoint.

## Features

- Run SWMM (pyswmm) and EPANET (WNTR) simulations, with optional progress callbacks
- Read and write SWMM / EPANET INP models
- Read SWMM RPT and OUT results, and EPANET BIN results
- Export simulation results to PostgreSQL (Giswater) or FROST
- Connect to PostgreSQL, SQLite, and GeoPackage

## Installation

```bash
pip install hydraulic-engine
```

From source:

```bash
git clone https://github.com/bgeo-gis/hydraulic-engine.git
cd hydraulic-engine
pip install -e .
```

For development:

```bash
pip install -e ".[dev]"
```

## Quick start

### Run a simulation

```python
import hydraulic_engine as he
from hydraulic_engine.utils.enums import RunStatus

# SWMM
swmm = he.swmm.SwmmRunner(inp_path="drainage.inp")
swmm_result = swmm.run()

# EPANET
epanet = he.epanet.EpanetRunner(inp_path="network.inp")
epanet_result = epanet.run()

if swmm_result.status == RunStatus.SUCCESS:
    print(f"SWMM finished in {swmm_result.duration_seconds:.2f}s")
```

Pass `progress_callback=fn` to either runner to receive progress updates during the run.

### Work with model files

```python
from hydraulic_engine.swmm import SwmmInpHandler
from hydraulic_engine.epanet import EpanetInpHandler

swmm_inp = SwmmInpHandler()
swmm_inp.load_file("drainage.inp")
print(swmm_inp.get_summary())

epanet_inp = EpanetInpHandler()
epanet_inp.load_file("network.inp")
print(epanet_inp.get_summary())
```

Result files use the matching handlers: `SwmmRptHandler`, `SwmmOutHandler`, and `EpanetBinHandler`.

### Export results

After a successful run, push results to the database or to FROST:

```python
import hydraulic_engine as he

runner = he.swmm.SwmmRunner(inp_path="drainage.inp")
runner.run()

dao = he.create_pg_connection(
    host="localhost",
    port=5432,
    dbname="hydraulic_db",
    user="user",
    password="pass",
    schema="my_schema",
)

runner.export_result(
    to=he.ExportDataSource.DATABASE,
    result_id="my_result",  # must already exist in rpt_cat_result
    client=dao,
)
```

The same `export_result(..., to=he.ExportDataSource.FROST)` path works for SensorThings via `he.create_frost_connection(...)`. EPANET uses the same pattern on `EpanetRunner`.

Exported SWMM time series follow what the model’s `[REPORT]` section requests (nodes, links, subcatchments).

### Database connections

```python
import hydraulic_engine as he

pg = he.create_pg_connection(host="localhost", port=5432, dbname="db", user="u", password="p")
gpkg = he.create_gpkg_connection("project.gpkg")
sqlite = he.create_sqlite_connection("local.db")

he.close_connection()
```

## Package layout

| Module | Role |
|--------|------|
| `hydraulic_engine.swmm` | SWMM runner, INP / RPT / OUT handlers |
| `hydraulic_engine.epanet` | EPANET runner, INP / BIN handlers |
| `hydraulic_engine.utils` | DB / API / logging helpers, `ExportDataSource`, `RunStatus` |
| `hydraulic_engine.config` | Package configuration |

## Development

```bash
pytest tests/
black src/
ruff check src/
```

## License

GNU General Public License v3.0 or later — see [LICENSE](LICENSE).

## Authors

**BGEO** — [info@bgeo.es](mailto:info@bgeo.es)
