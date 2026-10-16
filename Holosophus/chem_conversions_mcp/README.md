# MCP service for converting chemical representations through PubChem.

Supported directions:
- `iupac` -> `smiles`
- `name` (common/trivial) -> `smiles`
- `inchi` / `inchikey` -> `smiles`
- reverse from `smiles` -> `inchi` / `inchikey` / `iupac`

## Quick start

```bash
cd Holosophus/chem_conversions_mcp
uv sync
uv run python -m chem_conversions_mcp --transport streamable-http --host 0.0.0.0 --port 5061
```

The local Claude Compose stack publishes this service at
`http://127.0.0.1:5064/mcp`.

## Main MCP tool

- `convert_chemical_representation`
  - input: `value`, `source_format`, `target_format`
  - source formats: `smiles`, `inchi`, `inchikey`, `iupac`, `name`
  - target formats: `smiles`, `inchi`, `inchikey`, `iupac`, `name`

Note: conversion is network-backed (PubChem REST), so unavailable internet can fail requests.
