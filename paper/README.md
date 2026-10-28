# Manuscript for *Building Simulation*

*Py-CoSim: time-aware message-based co-simulation of building energy and artificial intelligence models*

| Path | Content |
|---|---|
| `manuscript/submission/PyCoSim_Manuscript_Anonymized.docx` | **Upload file**: anonymized manuscript (double-blind), with live EndNote citations |
| `manuscript/submission/PyCoSim_Manuscript_Anonymized.pdf` | PDF of the same, for reading |
| `manuscript/submission/PyCoSim_Title_Page.docx` | **Upload file**: title page with author/affiliation **placeholders** and the acknowledgements |
| `manuscript/submission/PyCoSim_Manuscript_Full.docx` | Full manuscript (title block + text), with live EndNote citations |
| `manuscript/Manuscript.docx` | Editable master with live EndNote citations, caption numbers and cross-references |
| `manuscript/paper_template.md`, `eval_draft.md`, `discussion_draft.md`, `impl_table_draft.md`, `fill.txt` | Markdown sources (assembled into `paper.md`) |
| `manuscript/references.ris` | 96 verified references (RIS; imported as EndNote citations; 60 are cited) |
| `figures/` | Figures at 600 dpi (`fig01`, `fig02`, `fig05`–`fig09` are used by this manuscript) |
| `experiments/` | Scripts that produce every number, table and figure; `run_all_paper.sh` runs them all |
| `notes/SMMM2_PLAN.md`, `notes/literature_check.md` | Working notes: contribution plan (written when the hub was still called SMMM) and the novelty check with verified prior art |
| `previous_v2/` | The earlier Py-CoSim 2.0 manuscript, kept for reference |

The format follows the `manuscript-template` skill: Times New Roman 12 pt, double spacing, numbered headings,
"Fig. 1"/"Table 1" captions, numbered EndNote citations. The anonymized file and title page were made with
`split_submission.py --anonymize --move "Acknowledgements"`.

## Rebuild

```bash
bash paper/experiments/run_all_paper.sh            # all experiments (~1.5 h; needs EnergyPlus 22.2)
```

Then, from `paper/manuscript/`, assemble `paper.md` from the sources and run the template's
`md2docx.py paper.md -o Manuscript.docx` and `split_submission.py` (see the skill's documentation).
Once you edit `Manuscript.docx` in Word, treat the Word file as the source.

## Before submission — checklist for the authors

- [ ] Fill in author names, affiliations, e-mail, funding and acknowledgements in the title page.
- [ ] In Word, choose an EndNote output style (Building Simulation uses author–year citations; the template writes
      numbered ones) and run *Update Citations and Bibliography*.
- [ ] Building Simulation asks for no field codes in the uploaded files: after the update, use EndNote >
      *Convert Citations and Bibliography* > *Convert to Plain Text* on a copy (or re-run `split_submission.py`
      with `--flatten-fields` on the md2docx output) for the upload.
- [ ] Declare the earlier SSRN preprint in the submission system if the journal asks about preprints.
- [ ] Optional but recommended: run one experiment with a hosted LLM (an API key is needed) to complement the
      stand-ins used in the AI experiments.
- [ ] Archive the repository release (e.g. Zenodo) and state the DOI in the submission system.
- [ ] Building Simulation asks for MathType equations; Word equations usually pass first submission.
