"""Remove relationships (hyperlinks, mailto links) that document.xml no longer uses from a .docx, in place.

split_submission.py --anonymize removes the author block but leaves its links (e.g. the corresponding
author's mailto: address) in word/_rels/document.xml.rels, where anyone who unzips the file can read them.

usage: python strip_unused_rels.py file.docx
"""
import re
import shutil
import sys
import zipfile


def main(path):
    with zipfile.ZipFile(path) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    data = {i.filename: d for i, d in items}
    doc = data["word/document.xml"].decode("utf-8")
    used = set(re.findall(r'r:(?:id|embed|link|pict)="([^"]+)"', doc))
    rels = data["word/_rels/document.xml.rels"].decode("utf-8")
    removed = []

    def keep(m):
        rel = m.group(0)
        rid = re.search(r'Id="([^"]+)"', rel).group(1)
        external = 'TargetMode="External"' in rel
        if external and rid not in used:
            removed.append(re.search(r'Target="([^"]*)"', rel).group(1))
            return ""
        return rel

    rels = re.sub(r"<Relationship [^>]*/>", keep, rels)
    data["word/_rels/document.xml.rels"] = rels.encode("utf-8")
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for info, _ in items:
            z.writestr(info, data[info.filename])
    shutil.move(tmp, path)
    print("removed %d unused external link(s): %s" % (len(removed), ", ".join(removed) or "-"))


if __name__ == "__main__":
    main(sys.argv[1])
