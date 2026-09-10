# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Add EPANET `EpanetControl` / `EpanetRule` and SWMM `SwmmControl` hybrid text models for create-or-replace via `update_inp_from_settings`.
- Add EPANET `get_rules()`, split `get_controls()` to simple controls only, and SWMM `get_controls()` with summary counts.
- Add GitHub Actions CI workflow that runs pytest on Python 3.9–3.12.

### Changed

- Align public EPANET/SWMM exports: `SwmmRunResult`, `EpanetFileHandler`, and `SimulationCancelled` are now part of the package API.
- Re-export `config` explicitly from `hydraulic_engine.config` for a clearer import surface.
- Hybrid `run()` error contract: `SimulationCancelled` / `SimulationError` (with optional `.result`) are raised for cancel and engine crashes; RPT/output `SUCCESS`/`WARNING`/`ERROR` still return a result. Giswater should catch `SimulationCancelled` for clean cancel UX.
- `validate_inp` raises `ModelNotLoadedError` / `FileLoadError` instead of returning an invalid dict for missing state/path.

### Fixed

- Align `EpanetInpHandler.get_summary()` with SWMM: return empty summary when no INP is loaded (no raise).

### Removed

- Remove unused `tools_config` stub (all functions raised `NotImplementedError`).

## [0.7.0] - 2026-07-31

### Added

- Add SWMM export to Giswater PostgreSQL via `SwmmRunner.export_result`, `SwmmRptHandler.export_to_database`, and `SwmmOutHandler.export_to_database`.
- Add shared SWMM export helpers in `swmm/export_db.py` (report selection, COPY streaming, clean/finalize).
- Add `SwmmInpHandler.get_report_element_selection()` so OUT time series follow the INP `[REPORT]` NODES / LINKS / SUBCATCHMENTS settings.
- Add SWMM and EPANET export tests.

### Changed

- Rewrite the README into a shorter package overview covering SWMM and EPANET run, file handling, and export.

## [0.6.0] - 2026-07-27

### Added

- Add cooperative simulation abort via `step_callback`: `RunStatus.CANCELLED`, `SimulationCancelled`, and full early exit in EPANET (hydraulics + water quality) and SWMM runners when the callback returns `False`.

### Changed

- Refactor `EpanetBinHandler.export_to_database` for large networks: vectorized unit conversion, PostgreSQL `COPY` streaming, vectorized stats, staging-table inserts, and a faster `only_extrema` path.

## [0.5.0] - 2026-07-24

### Added

- Add `only_extrema` parameter to `EpanetBinHandler.export_to_database` to skip time series inserts and only export aggregated stats.

## [0.4.1] - 2026-06-23

### Fixed

- Add no-op `__post_init__` on `EpanetBaseObject` so `EpanetPipe` partial construction (e.g. `EpanetPipe(roughness=...)`) no longer raises `AttributeError` on `super().__post_init__()`.

## [0.4.0] - 2026-05-25

### Added

- New exceptions: `ModelNotLoadedError`, `ValidationError`, `DatabaseError`, `APIError`, `ExportError`, `SimulationError`.
- Basic `__post_init__` validation on EPANET/SWMM model dataclasses.

### Changed

- Refactored error handling across all modules to raise exceptions instead of silently returning fallback values (`None`, `False`, `0`, `{}`).
- Improved consistency with Python error-handling best practices.

## [0.3.2] - 2026-04-30

### Changed

- Improve error handling with custom hydraulic-engine exceptions

### Added

- Usable example scripts for the package

## [0.3.1] - 2026-03-04

### Changed

- Improve post process arcs performance for EPANET export to Database

## [0.3.0] - 2026-03-02

### Changed

- Make package compatible with Python 3.9
- Make export to database for EPANET compatible with Giswater 3.5

## [0.2.0] - 2026-01-22

### Added

- Export results to database for EPANET

## [0.1.0] - 2026-01-16

### Added

- This CHANGELOG.md file
- Package folder structure
- Database connection files
- SWMM runner file
- SWMM inp, rpt and out handler files
- Parameter management to the SWMM simulation
- SWMM models file
- EPANET runner file
- EPANET inp, rpt and out handler files
- Parameter management to the EPANET simulation
- EPANET models file
- **FROST-Server / SensorThings API integration**
  - `tools_api.py`: Abstract API client framework with FROST-Server implementation
  - `tools_sensorthings.py`: High-level helper functions for SensorThings API operations
  - `create_frost_connection()`: Global API client connection management
  - `export_to_frost()`: Export SWMM/EPANET simulation results to FROST-Server
  - Support for batch operations to efficiently create/update Things, Datastreams, and Observations
  - Integration with existing export framework (`ExportDataSource.FROST`)

[unreleased]: https://github.com/bgeo-gis/hydraulic-engine/compare/v0.7.0...main
[0.7.0]: https://github.com/bgeo-gis/hydraulic-engine/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/bgeo-gis/hydraulic-engine/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/bgeo-gis/hydraulic-engine/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/bgeo-gis/hydraulic-engine/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/bgeo-gis/hydraulic-engine/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/bgeo-gis/hydraulic-engine/compare/v0.3.1...0.3.2
[0.3.1]: https://github.com/bgeo-gis/hydraulic-engine/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/bgeo-gis/hydraulic-engine/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/bgeo-gis/hydraulic-engine/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/bgeo-gis/hydraulic-engine/releases/tag/v0.1.0
