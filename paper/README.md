# Manuscript for *Building Simulation*

*Py-CoSim: time-aware message-based co-simulation for building energy systems*

| Path | Content |
|---|---|
| `manuscript/submission/PyCoSim_Manuscript_Anonymized.docx` | **Upload file** (Blinded Manuscript): no author information, author–year citations as plain text, no Word fields |
| `manuscript/submission/PyCoSim_Manuscript_Anonymized.pdf` | PDF of the same, for reading |
| `manuscript/submission/PyCoSim_Title_Page.docx` | **Upload file** (Title Page): cover letter, title block, author contributions, ethics statements, acknowledgements, declarations |
| `manuscript/submission/PyCoSim_code_anonymized.zip` | **Upload file** (Electronic Supplementary Material, for review): the repository without author names or links |
| `manuscript/submission/PyCoSim_Manuscript_Full.docx` | Full manuscript with live EndNote citations (authors' master, not uploaded) |
| `manuscript/Manuscript.docx` | Same as the full manuscript (editable master) |
| `manuscript/paper_template.md`, `eval_draft.md`, `discussion_draft.md`, `impl_table_draft.md`, `fill.txt` | Markdown sources |
| `manuscript/tools/` | Build scripts: `assemble.py`, `md2docx_bs.py` (Building Simulation author–year style), `title_page_bs.py`, `make_anonymous_code.py` |
| `manuscript/checks/` | Compliance checks against the journal's Instructions for Authors |
| `manuscript/references.ris` | Verified references (RIS; imported as EndNote citations) |
| `figures/` | Figures at 600 dpi (`fig01`, `fig02`, `fig05`–`fig09` are used by this manuscript) |
| `experiments/` | Scripts that produce every number, table and figure; `run_all_paper.sh` runs them all |
| `notes/SMMM2_PLAN.md`, `notes/literature_check.md` | Working notes: contribution plan (written when the hub was still called SMMM) and the novelty check with verified prior art |
| `previous_v2/` | The earlier Py-CoSim 2.0 manuscript, kept for reference |

The format follows the `manuscript-template` skill (Times New Roman 12 pt, double spacing, numbered headings,
"Fig. 1"/"Table 1" captions) with Building Simulation's name–year citations and reference list.

## Rebuild

```bash
bash paper/experiments/run_all_paper.sh            # all experiments (~1.5 h; needs EnergyPlus 22.2)
```

Then, from `paper/manuscript/` (S = the manuscript-template skill's `scripts` folder):

```bash
python tools/assemble.py                                    # paper.md and paper_blind.md
python tools/md2docx_bs.py paper.md -o Manuscript.docx       # full master, live EndNote fields
python tools/md2docx_bs.py paper_blind.md -o blind.docx
python $S/split_submission.py blind.docx --out-dir out --anonymize --move "Acknowledgements" --flatten-fields
python tools/strip_unused_rels.py out/blind_Anonymized.docx  # drops the hidden mailto: link of the author block
python tools/make_anonymous_code.py                          # submission/PyCoSim_code_anonymized.zip
```

`out/blind_Anonymized.docx` is the upload file. The title page is edited by hand in Word;
`tools/title_page_bs.py` added its cover letter and declarations once. Once you edit a Word file, treat it as the source.

## Before submission — checklist for the authors

- [ ] Title page: confirm the yellow-highlighted text (author contributions, the statement that no text or figures
      are reused from the SSRN preprint, the generative-AI declaration), then remove the highlighting.
- [ ] Title page: check each co-author's primary affiliation (the guide wants the institution where most of the work
      was done, with a later move given as the present address).
- [ ] Editorial Manager: enter the NSF funding (Award No. 2623038), add ORCID iDs, choose classification 5.3, and upload
      `PyCoSim_code_anonymized.zip` as supplementary material for review.
- [ ] Optional: Building Simulation asks for MathType equations; Word equations usually pass first submission.
- [ ] Optional but recommended: run one experiment with a hosted LLM (an API key is needed) to complement the
      stand-ins used in the AI experiments.
- [ ] Archive the repository release (e.g. Zenodo) and state the DOI in the submission system.
