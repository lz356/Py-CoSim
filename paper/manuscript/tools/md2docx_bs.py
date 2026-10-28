"""Build the manuscript .docx in Building Simulation's name-year reference style.

Wraps md2docx.py of the manuscript-template skill (EndNote-linked citations) and changes only how
citations and the reference list look (Building Simulation, Instructions for Authors, "References"):

    [@key]          -> (Thompson 1990)  (Becker and Seligman 1996)  (Harris et al. 2001)
    [@a; @b]        -> (Thompson 1990a, b; Becker and Seligman 1996)
    [-@key]         -> (1990), for "Thompson (1990) showed ..."
    reference list  -> alphabetical by first author, in the journal's format

usage:  python md2docx_bs.py paper.md -o Manuscript.docx
The skill's scripts folder is taken from MANUSCRIPT_TEMPLATE_SCRIPTS or ~/.claude/skills/manuscript-template/scripts.
"""
import argparse
import datetime
import os
import re
import sys
import unicodedata
from xml.sax.saxutils import escape

SKILL = os.environ.get("MANUSCRIPT_TEMPLATE_SCRIPTS",
                       os.path.join(os.path.expanduser("~"), ".claude", "skills", "manuscript-template", "scripts"))
sys.path.insert(0, SKILL)
import md2docx as M     # noqa: E402
import endnote as EN    # noqa: E402
import ooxml as X       # noqa: E402

# accept [-@key] (author suppressed) in citation groups
_pat = M.INLINE.pattern
_new = _pat.replace(r"(?P<cbody>@", r"(?P<cbody>-?@").replace(r"[;,]\s*@", r"[;,]\s*-?@")
assert _new != _pat, "md2docx citation pattern changed; update md2docx_bs.py"
M.INLINE = re.compile(_new)

YEAR = {}        # key -> "2001" or "2001a"


# ---------------------------------------------------------------- names and labels
def fold(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def last(a):
    return a.split(",")[0].strip() if "," in a else a.strip()


def authors_of(r):
    return r.authors or ([r.get("publisher")] if r.get("publisher") else ["Anonymous"])


def label(r):
    a = authors_of(r)
    if len(a) == 1:
        return last(a[0])
    if len(a) == 2:
        return "%s and %s" % (last(a[0]), last(a[1]))
    return "%s et al." % last(a[0])


def sort_key(r):
    a = authors_of(r)
    n = min(len(a), 3)
    second = fold(last(a[1])) if len(a) == 2 else ""
    first_initials = fold(person(a[0]))
    return (fold(last(a[0])), first_initials, n, second, r.get("year") or "9999", YEAR.get(r.key, ""),
            fold(r.get("title")))


def assign_years(refs):
    """Year labels with a, b, ... when the same author label has several works in one year."""
    groups = {}
    for r in refs:
        groups.setdefault((label(r), r.get("year") or "n.d."), []).append(r)
    for (lab, y), rs in groups.items():
        if len(rs) == 1:
            YEAR[rs[0].key] = y
        else:
            for i, r in enumerate(sorted(rs, key=lambda r: fold(r.get("title")))):
                YEAR[r.key] = y + "abcdefghijklmnopqrstuvwxyz"[i]


def display(items):
    """items: [(ref, author_suppressed)] -> '(A 2001; B and C 2002a, b)'"""
    parts = []                      # [label or None, [years]]
    for r, sup in items:
        y = YEAR.get(r.key) or r.get("year") or "n.d."
        lab = None if sup else label(r)
        if parts and lab is not None and parts[-1][0] == lab:
            prev = parts[-1][1][-1]
            parts[-1][1].append(y[4:] if y[:4] == prev[:4] and len(y) > 4 and len(prev) > 4 else y)
        else:
            parts.append([lab, [y]])
    return "(" + "; ".join(("%s %s" % (lab, ", ".join(ys))) if lab else ", ".join(ys) for lab, ys in parts) + ")"


# ---------------------------------------------------------------- EndNote citation field
def cite_xml(items, disp, ts):
    out = ["<EndNote>"]
    for k, (r, sup) in enumerate(items):
        out.append("<Cite%s><Author>%s</Author>%s<RecNum>%s</RecNum>%s%s</Cite>" % (
            ' ExcludeAuth="1"' if sup else "", escape(EN.first_author_last(r)),
            "<Year>%s</Year>" % escape(r.get("year")) if r.get("year") else "", escape(r.rec_number),
            "<DisplayText>%s</DisplayText>" % escape(disp) if k == 0 else "", EN.record_xml(r, ts)))
    out.append("</EndNote>")
    return "".join(out)


def citation_field(items, disp, ts):
    import base64
    xml = cite_xml(items, disp, ts)
    res = '<w:r><w:rPr><w:noProof/></w:rPr><w:t xml:space="preserve">%s</w:t></w:r>' % escape(disp)
    if len(xml) <= EN.LONG_XML:
        return ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                '<w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE %s</w:instrText></w:r>'
                '<w:r><w:fldChar w:fldCharType="separate"/></w:r>%s'
                '<w:r><w:fldChar w:fldCharType="end"/></w:r>' % (escape(xml), res))
    b64 = base64.b64encode(xml.encode("utf-8") + b"\x00").decode("ascii")
    b64 = "\r\n".join(b64[i:i + 76] for i in range(0, len(b64), 76))
    data = '<w:fldData xml:space="preserve">%s</w:fldData>' % b64
    return ('<w:r><w:fldChar w:fldCharType="begin">%s</w:fldChar></w:r>'
            '<w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="begin">%s</w:fldChar></w:r>'
            '<w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE.DATA </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>%s'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>' % (data, data, res))


def cite_group(self, body, fmt):
    if not self.bib:
        M.WARN.append("citation [%s] but no 'bibliography:' file in the front matter" % body)
        return X.run(M.smart("[" + body + "]"), **self._f(fmt))
    items = []
    for k in (s.strip() for s in re.split(r"[;,]", body)):
        if not k:
            continue
        sup = k.startswith("-")
        k = k.lstrip("-").lstrip("@")
        r = self.bib.get(k)
        if r is None:
            M.WARN.append("unknown citation key @%s" % k)
            continue
        if k not in self.cite_num:
            self.cite_order.append(r)
            self.cite_num[k] = len(self.cite_order)
        items.append((r, sup))
    if not items:
        return X.run("(??)", **self._f(fmt))
    return citation_field(items, display(items), self.ts)


# ---------------------------------------------------------------- reference list entries
def person(a):
    if "," not in a:
        return a.strip()                                   # organisation
    sur, given = [s.strip() for s in a.split(",", 1)]
    ini = "".join(p[0].upper() for p in re.split(r"[\s.\-]+", given) if p and p[0].isalpha())
    return ("%s %s" % (sur, ini)).strip()


def author_list(r):
    a = r.authors
    if not a:
        return r.get("publisher") or "Anonymous"
    if len(a) > 3:
        return ", ".join(person(x) for x in a[:3]) + ", et al."
    return ", ".join(person(x) for x in a)


def sentence(s):
    s = (s or "").strip()
    return s if not s or s[-1] in ".?!" else s + "."


def dash(p):
    return re.sub(r"\s*-+\s*", "–", p or "")


def accessed(s):
    try:
        return datetime.date.fromisoformat(s).strftime("%d %b %Y").lstrip("0")
    except ValueError:
        return s


def entry(r):
    head = "%s (%s). %s" % (author_list(r), YEAR.get(r.key) or r.get("year") or "n.d.", sentence(r.get("title")))
    t, src = r.ris, r.get("secondary")
    pub, place, pages = r.get("publisher"), r.get("place"), dash(r.get("pages"))
    doi = "https://doi.org/%s" % r.get("doi") if r.get("doi") else ""
    url = r.get("url")
    org_pub = pub if pub and pub not in r.authors else ""
    if t in ("JOUR", "EJOUR"):
        if r.get("volume") and pages:
            return "%s %s, %s: %s." % (head, src, r.get("volume"), pages)
        return "%s %s, %s." % (head, src, doi or url or "in press")
    if t in ("CONF", "CPAPER"):
        tail = ", ".join(p for p in (src, place, "pp.%s" % pages if pages else "") if p)
        return "%s In: %s." % (head, tail) + (" %s." % doi if doi and not pages else "")
    if t in ("BOOK", "EBOOK"):
        return "%s %s" % (head, sentence(": ".join(p for p in (place, pub) if p)))
    if t in ("CHAP", "ECHAP"):
        eds = ""
        if r.editors:
            eds = "%s (%s), " % (", ".join(person(e) for e in r.editors), "ed" if len(r.editors) == 1 else "eds")
        where = ": ".join(p for p in (place, pub) if p)
        return "%s In: %s%s. %s" % (head, eds, src, sentence(where)) + (" pp. %s." % pages if pages else "")
    if t == "THES":
        return "%s %s" % (head, sentence(", ".join(p for p in (r.get("thesis_type") or "PhD Thesis", pub) if p)))
    if t == "RPRT":
        kind = r.get("thesis_type") or "Report"
        bits = []
        if src and kind.lower() == "presentation":
            bits.append("Presented at %s." % src.rstrip("."))
            kind = ""
        num = " ".join(p for p in (kind, r.get("isbn")) if p)
        if num:
            bits.append(sentence(num))
        where = ": ".join(p for p in (place, pub) if p)
        if where:
            bits.append(sentence(where))
        if doi:
            bits.append(doi + ".")
        elif url:
            bits.append("Available at %s." % url)
        return " ".join([head] + bits)
    # online documents, standards, data, software, generic (example k of the guide)
    bits = [head]
    if src and src != r.get("title"):
        bits.append(("In: %s." if t in ("ELEC", "WEB") else "%s.") % src.rstrip("."))
    if org_pub:
        bits.append(sentence(org_pub))
    if doi:
        bits.append(doi + ".")
    elif url:
        bits.append("Available at %s." % url)
        if r.get("accessed"):
            bits.append("Accessed %s." % accessed(r.get("accessed")))
    return " ".join(bits)


def bibliography_xml(self):
    refs = sorted(self.cite_order, key=sort_key)
    out = []
    for i, r in enumerate(refs, 1):
        inner = X.run(entry(r))
        if i == 1:
            inner = ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                     '<w:r><w:instrText xml:space="preserve"> ADDIN EN.REFLIST </w:instrText></w:r>'
                     '<w:r><w:fldChar w:fldCharType="separate"/></w:r>') + inner
        if i == len(refs):
            inner += '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        out.append(X.para("EndNoteBibliography", inner))
    return "".join(out)


M.Doc.cite_group = cite_group
M.Doc.bibliography_xml = bibliography_xml


def cited_keys(md_text):
    keys = []
    for body in re.findall(r"\[(-?@[A-Za-z0-9_.\-]+(?:\s*[;,]\s*-?@[A-Za-z0-9_.\-]+)*)\]", md_text):
        for k in re.split(r"[;,]", body):
            k = k.strip().lstrip("-").lstrip("@")
            if k and k not in keys:
                keys.append(k)
    return keys


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("markdown")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    text = open(a.markdown, encoding="utf-8").read()
    bib = re.search(r"^bibliography:\s*(.+)$", text, re.M)
    if bib:
        bp = bib.group(1).strip()
        bp = bp if os.path.isabs(bp) else os.path.join(os.path.dirname(os.path.abspath(a.markdown)), bp)
        lib = {r.key: r for r in EN.load(bp)}
        assign_years([lib[k] for k in cited_keys(text) if k in lib])
    M.WARN.clear()
    M.build(a.markdown, a.out)
    for w in M.WARN:
        print("warning:", w)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
