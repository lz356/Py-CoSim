# Manuscript for *Building Simulation*: SMMM 2.0

*Exchanging messages instead of wiring software: time-aware co-simulation of building energy models with
data-driven and artificial intelligence components*

| Path | Content |
|---|---|
| `manuscript/submission/PyCoSim_SMMM_Manuscript_Anonymized.docx` | **Upload file**: anonymized manuscript (double-blind), field codes flattened as the journal asks |
| `manuscript/submission/PyCoSim_SMMM_Manuscript_Anonymized.pdf` | PDF of the same, for reading |
| `manuscript/submission/PyCoSim_SMMM_Title_Page.docx` | **Upload file**: title page with author/affiliation **placeholders** and the acknowledgements |
| `manuscript/submission/PyCoSim_SMMM_Manuscript_Full.docx` | Full manuscript (title block + text), fields flattened |
| `manuscript/Manuscript.docx` | Editable master with live EndNote citations, caption numbers and cross-references |
| `manuscript/paper_template.md`, `eval_draft.md`, `discussion_draft.md`, `impl_table_draft.md`, `fill.txt` | Markdown sources (assembled into `paper.md`) |
| `manuscript/references.ris` | 96 verified references (RIS; imported as EndNote citations) |
| `figures/` | Figures at 600 dpi (`fig01`, `fig02`, `fig05`–`fig09` are used by this manuscript) |
| `experiments/` | Scripts that produce every number, table and figure; `run_all_smmm.sh` runs them all |
| `notes/SMMM2_PLAN.md` | Contribution plan; `notes/literature_check.md`: novelty check with verified prior art |
| `previous_v2/` | The earlier Py-CoSim 2.0 manuscript, kept for reference |

The format follows the `manuscript-template` skill: Times New Roman 12 pt, double spacing, numbered headings,
"Fig. 1"/"Table 1" captions, numbered EndNote citations; the anonymized file and title page were made with
`split_submission.py --anonymize --flatten-fields --move "Acknowledgements"` as Building Simulation requires.

## Rebuild

```bash
bash paper/experiments/run_all_smmm.sh            # all experiments (~1.5 h; needs EnergyPlus 22.2)
```

Then, from `paper/manuscript/`, assemble `paper.md` from the sources and run the template's
`md2docx.py paper.md -o Manuscript.docx` and `split_submission.py` (see the skill's documentation).
Once you edit `Manuscript.docx` in Word, treat the Word file as the source.

## Before submission — checklist for the authors

- [ ] Fill in author names, affiliations, e-mail, funding and acknowledgements in the title page.
- [ ] In `Manuscript.docx`, choose an EndNote output style (Building Simulation uses author–year citations;
      the template writes numbered ones) and run *Update Citations and Bibliography*; then re-split.
- [ ] Decide whether to keep the tool name in the anonymized file (reviewers can find the public repository).
- [ ] Optional but recommended: run one experiment with a hosted LLM (an API key is needed) to complement the
      stand-ins used in the AI experiments.
- [ ] Archive the repository release (e.g. Zenodo) and state the DOI in the submission system.
- [ ] Building Simulation asks for MathType equations; Word equations usually pass first submission.
