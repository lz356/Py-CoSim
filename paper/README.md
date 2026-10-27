# Manuscript for *Building Simulation*

| File | Content |
|---|---|
| `Py-CoSim_BuildingSimulation_Manuscript_Blinded.docx` | Blinded manuscript (no author information; double-blind review) |
| `Py-CoSim_BuildingSimulation_TitlePage.docx` | Cover letter, title page, author/affiliation **placeholders**, declarations |
| `figures/` | Figures at 600 dpi (PNG) |
| `experiments/` | Scripts that produce every number, table and figure of the paper |
| `build_manuscript.py` | Builds both Word files from the text and the experiment results |
| `references.py` | Reference list (Building Simulation author–year style), checked against Crossref/publisher/OSTI records |

## Reproduce

```bash
bash paper/experiments/run_all.sh      # ~30-40 min on a laptop; needs EnergyPlus 22.2
python paper/build_manuscript.py       # regenerates the two .docx files
```

Experiments: `verification.py` (fidelity vs stand-alone EnergyPlus), `scalability.py` (overhead, parallel speed-up,
data-exchange micro-benchmark), `overhead_breakdown.py` (fixed vs per-step overhead),
`case_a_community.py`, `case_b_llm.py`, `case_c_travel.py`, `diagrams.py`.
Raw outputs are written to `experiments/results/` (large run folders are git-ignored; the summary CSV/JSON files
are kept).

## Before submission — checklist for the authors

- [ ] Fill in author names, affiliations, CRediT roles, funding and acknowledgements in the title page.
- [ ] Copy the text into the journal's 2025 Word template (Blinded Manuscript / Title Page) if required by the
      editorial office; equations are Word (OMML) equations — the journal prefers MathType.
- [ ] Archive the repository release (e.g. Zenodo) and add the DOI to the code-availability statement.
- [ ] Case B uses the offline stand-in backend; consider adding a run with a real LLM
      (`examples/05_llm_supervisor/run.py --backend anthropic --record ...`) and reporting it.
- [ ] Review the generative-AI disclosure statement on the title page.
- [ ] Optional: a graphical abstract (5 × 8 cm, ≥600 dpi) — `figures/fig01_architecture.png` is a starting point.
