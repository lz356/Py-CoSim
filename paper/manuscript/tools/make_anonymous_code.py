"""Make an anonymized copy of the Py-CoSim repository for double-blind review (Electronic Supplementary Material).

Takes the files tracked by git (as they are in the working tree), leaves out the manuscript folders, removes
author names, e-mail, user name, repository links and local paths, and refuses to write the archive if any
identifying text is left.

usage: python make_anonymous_code.py [-o PyCoSim_code_anonymized.zip]
"""
import argparse
import re
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EXCLUDE_DIRS = ("paper/manuscript/", "paper/previous_v2/", "paper/notes/")
EXCLUDE_FILES = {"CITATION.cff", "paper/README.md", "paper/references.py"}
WITHHELD = "<repository link withheld for double-blind review>"

GENERIC = [
    (re.compile(r"https?://github\.com/lz356/Py-CoSim(\.git)?"), WITHHELD),
    (re.compile(r"lz356"), "anonymous"),
    (re.compile(r"liangzhang1@arizona\.edu"), "anonymous@example.org"),
    (re.compile(r"C:[\\/]+Users[\\/]+liangzhang1", re.I), r"C:\\Users\\user"),
]
SPECIFIC = {
    "LICENSE": [("Copyright (c) 2022-2026 Liang Zhang and Py-CoSim contributors",
                 "Copyright (c) 2022-2026 The Py-CoSim authors (names withheld for double-blind review)")],
    "pyproject.toml": [('authors = [{ name = "Liang Zhang", email = "liangzhang1@arizona.edu" }]',
                        'authors = [{ name = "Anonymous (withheld for double-blind review)" }]')],
    "CHANGELOG.md": [("described in the SSRN preprint 4572925", "described in an earlier preprint (withheld for review)")],
    ".gitignore": [("Liang's Working Folder/\n", "")],
}
SECTIONS = {   # file: (start marker, end marker) -> replaced by a short note
    "README.md": ("## Citing\n", "## Version history"),
    "docs/faq.md": ("## How to cite\n", "If you use the occupancy data"),
}
NOTE = "Citation details are withheld during double-blind review.\n\n"
CHECK = re.compile(r"lz356|liangzhang|\bLiang\b|Haroon|Alyssa|\bRyan\b|arizona\.edu|SSRN|4572925", re.I)


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def scrub(rel, data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data                                     # binary file
    nl = "\r\n" if "\r\n" in text else None
    if nl:
        text = text.replace("\r\n", "\n")
    for old, new in SPECIFIC.get(rel, []):
        if old not in text:
            raise SystemExit("%s: text to replace not found: %r" % (rel, old[:60]))
        text = text.replace(old, new)
    if rel in SECTIONS:
        a, b = SECTIONS[rel]
        i, j = text.index(a), text.index(b)
        text = text[:i + len(a)] + "\n" + NOTE + text[j:]
    for pat, rep in GENERIC:
        text = pat.sub(rep, text)
    if nl:
        text = text.replace("\n", nl)
    return text.encode("utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=str(REPO / "paper" / "manuscript" / "submission" / "PyCoSim_code_anonymized.zip"))
    a = ap.parse_args()
    files, leaks = [], []
    for rel in tracked():
        if rel.startswith(EXCLUDE_DIRS) or rel in EXCLUDE_FILES:
            continue
        path = REPO / rel
        if not path.is_file():
            continue
        data = scrub(rel, path.read_bytes())
        try:
            for n, line in enumerate(data.decode("utf-8").splitlines(), 1):
                if CHECK.search(line):
                    leaks.append("%s:%d: %s" % (rel, n, line.strip()[:120]))
        except UnicodeDecodeError:
            if re.search(rb"lz356|liangzhang|Haroon", data):
                leaks.append("%s: binary file contains an identifying string" % rel)
        files.append((rel, data))
    if leaks:
        print("identifying text left; archive not written:")
        print("\n".join(leaks))
        sys.exit(1)
    with zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in files:
            z.writestr("Py-CoSim/" + rel, data)
    print("wrote %s (%d files, %.1f MB)" % (a.out, len(files), Path(a.out).stat().st_size / 1e6))


if __name__ == "__main__":
    main()
