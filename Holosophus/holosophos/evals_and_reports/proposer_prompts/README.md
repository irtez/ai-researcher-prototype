# Proposer prompt registry

Named prompt variants for the proposer ablation harness.

Each `.txt` file holds one prompt. Filename is the variant name referenced
in `proposer_variants.yaml` (without extension), e.g. `improvement_default`.

Naming convention:
- `extract_*.txt`  — for `extract_bitflip_info`
- `improvement_*.txt` — for `generate_research_proposals`
- `improvement_system_*.txt` — system prompt paired with `improvement_*.txt`
- `score_*.txt` — for `score_research_proposals`

The `*_default.txt` files are byte-equivalent to the prompts shipped in
`academia_patches/bitflip.py`. To compare against a custom prompt, drop a new
file here and reference it from a variant block:

```yaml
generate:
  prompt: improvement_chain_of_ideas
```

Templates use Jinja2 syntax (`{{var}}`, `{% for %}`); they are rendered by
`academia_mcp.utils.encode_prompt`.