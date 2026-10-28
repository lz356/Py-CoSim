"""Assemble paper.md (full) and paper_blind.md (double-blind review copy) from the section drafts.

The blind copy replaces the public repository link, which identifies the authors, by a pointer to the
anonymized code archive submitted as Electronic Supplementary Material.
"""
import re
from pathlib import Path

M = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/lz356/Py-CoSim"

paper = (M / "paper_template.md").read_text(encoding="utf-8")
evald = (M / "eval_draft.md").read_text(encoding="utf-8")
impl = (M / "impl_table_draft.md").read_text(encoding="utf-8")
disc = (M / "discussion_draft.md").read_text(encoding="utf-8")
fill = (M / "fill.txt").read_text(encoding="utf-8") if (M / "fill.txt").exists() else ""

# implementation paragraph + feature table
impl_par, table = impl.split("Table: Comparison", 1)
table = "Table: Comparison" + table
paper = paper.replace("@@IMPL_REST@@", impl_par.strip())
paper = paper.replace("@@TABLE_FEATURES@@", table.strip())
paper = paper.replace("@@EVAL@@", evald.strip())

# discussion + conclusions (the draft contains its own "# Conclusions" heading)
disc_body, concl = disc.split("# Conclusions {#sec:conclusions}", 1)
paper = paper.replace("@@DISCUSSION@@", disc_body.strip())
paper = paper.replace("# Conclusions {#sec:conclusions}\n\n@@CONCLUSIONS@@", "# Conclusions {#sec:conclusions}\n\n" + concl.strip())

# simple key=value fills: lines "@@KEY@@ = text" separated by "\n====\n"
for block in fill.split("\n====\n"):
    if "=" not in block:
        continue
    key, val = block.split("=", 1)
    paper = paper.replace(key.strip(), val.strip())
left = sorted(set(re.findall(r"@@[A-Z0-9_]+@@", paper)))
(M / "paper.md").write_text(paper, encoding="utf-8")

# double-blind copy: no repository link
BLIND = [
    (" (%s), in which" % REPO_URL, ", in which"),
    ("are available at %s. Its core" % REPO_URL,
     "are openly available in a public repository, whose link is withheld for double-blind review; an anonymized "
     "copy is provided as Electronic Supplementary Material (ESM). Its core"),
    ("are openly available at %s." % REPO_URL,
     "are openly available; an anonymized copy is provided as Electronic Supplementary Material for review."),
]
blind = paper
for old, new in BLIND:
    if blind.count(old) != 1:
        raise SystemExit("blind copy: expected one match for %r" % old)
    blind = blind.replace(old, new)
if "lz356" in blind:
    raise SystemExit("blind copy still contains the repository user name")
(M / "paper_blind.md").write_text(blind, encoding="utf-8")

print("remaining placeholders:", left)
print("words (rough):", len(re.sub(r"\[-?@[^\]]*\]", "", paper).split()))
