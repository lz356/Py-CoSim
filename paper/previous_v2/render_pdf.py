"""Convert the manuscript .docx files to PDF with Microsoft Word (Windows) for proofreading.

    python paper/render_pdf.py [--png OUTDIR]
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def to_pdf(docx: Path) -> Path:
    pdf = docx.with_suffix(".pdf")
    ps = (
        "$w = New-Object -ComObject Word.Application; $w.Visible = $false; "
        f"$d = $w.Documents.Open('{docx}', $false, $true); "
        "$d.Fields.Update() | Out-Null; "
        f"$d.SaveAs([ref] '{pdf}', [ref] 17); $d.Close($false); $w.Quit()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=300)
    return pdf


def main() -> None:
    pngdir = Path(sys.argv[sys.argv.index("--png") + 1]) if "--png" in sys.argv else None
    for docx in sorted(HERE.glob("Py-CoSim_BuildingSimulation_*.docx")):
        pdf = to_pdf(docx)
        print("wrote", pdf)
        if pngdir is not None:
            import fitz  # PyMuPDF

            pngdir.mkdir(parents=True, exist_ok=True)
            doc = fitz.open(pdf)
            for i, page in enumerate(doc):
                page.get_pixmap(dpi=60).save(pngdir / f"{docx.stem[-12:]}_p{i + 1:02d}.png")
            print(f"  {len(doc)} pages -> {pngdir}")


if __name__ == "__main__":
    main()
