# Chemistry MCP

Chemical structure analysis, PubChem, ChemX tables, file processing and ADME
tools for AI Researcher. The MCP server runs with `python serve_http.py`.

The source registry is `datasets.yaml`. To prepare sources marked `preload`,
run `python -m prepare_datasets --preload`; it does not start MCP or an LLM.
Set `CHEM_DATA_DIR` to persistent storage. SQL queries reuse prepared tables.

Shared MCP tools: `list_datasets`, `describe_dataset`, `query_dataset_sql`,
and `search_connected_datasets` for RDKit search across registered datasets and
session files. Legacy ChemX names remain callable for old clients but are hidden
from `tools/list`; new agents use the shared catalog and SQL tool.

Deployment and dataset registration instructions live in the integrated repo:
`docs/operations/chemistry-local.md` and `docs/operations/chemistry-datasets.md`.

## ADMET-AI response

`profile_molecule_physchem_and_adme_nn` keeps its `ok` / `results` envelope and
raw `adme_predictions`. It additionally returns:

- `model`: installed package version and SHA-256 identity of the bundled ensemble;
- `property_metadata`: names, task types, units, declared ranges and source links
  from the installed `admet_ai/resources/data/admet.csv` (not a second hand-written
  endpoint catalog or a claim about prediction accuracy);
- `interpretation`: common rules for classification/regression and cached results;
- `cached` and `prediction_warnings` for each molecule. Out-of-range values are
  retained and flagged, never clamped or silently converted.

The cache is keyed by model identity and canonical **isomeric** SMILES. Equivalent
SMILES share cached predictions, while stereoisomers remain distinct. A mixed
batch can contain cached and newly calculated rows; duplicates within a fresh
batch share one computation and remain `cached=false`. Old unversioned cache
rows are preserved but not reused as results of the current model.

Classification outputs are model probability scores, not measured absorption
fractions. Package metadata for `Caco2_Wang` currently declares
`log(10^-6 cm/s)`; the response marks this scale as needing verification before
conversion. Neither this label nor a negative VDss prediction is silently
reinterpreted. Source definitions: [ADMET-AI catalog](https://github.com/swansonk14/admet_ai/blob/main/admet_ai/resources/data/admet.csv)
and [TDC ADME endpoints](https://tdcommons.ai/single_pred_tasks/adme/).

## Heuristic ADME response

`profile_molecule_physchem_and_adme_heuristic` preserves `ok`, `results` and
all existing numeric values. It returns method metadata once per batch:

- `method`: local method identifier, installed RDKit version and rounding rules;
- `descriptor_units`: units of the inputs used by the formulas;
- `property_metadata`: the actual logS expression, HIA/Caco-2/BBB checks and
  thresholds, Lipinski violation count and the ordered bioavailability lookup;
- `interpretation`: scores are rule-based estimates, not probabilities, percentages
  or measurements. No validated applicability domain or accuracy is claimed.

This describes the implemented local heuristic, not the original Delaney ESOL
equation or the neural-network predictor. Classification uses scores rounded to
six decimals before comparison with thresholds, preserving the existing behavior.
Metadata/implementation agreement is checked on molecules including boundary cases.
