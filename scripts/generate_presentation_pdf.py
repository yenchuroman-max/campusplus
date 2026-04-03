"""Generate a multi-page PDF from the VKR presentation.

Each slide becomes a full-page landscape screenshot, then all are merged
into a single PDF file.

Usage:  py -3 scripts/generate_presentation_pdf.py
Output: campusplus_presentation_vkr_2026.pdf  (in project root)
"""
import sys, pathlib, tempfile, shutil

def main():
    from playwright.sync_api import sync_playwright

    root = pathlib.Path(__file__).resolve().parent.parent
    html_path = root / "campusplus_presentation_vkr_2026.html"
    out_pdf = root / "campusplus_presentation_vkr_2026.pdf"

    if not html_path.exists():
        print(f"ERROR: {html_path} not found"); sys.exit(1)

    file_url = html_path.as_uri()
    print(f"Opening {file_url}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(file_url, wait_until="networkidle")
        page.wait_for_timeout(1000)

        # Count slides
        total = page.evaluate("document.querySelectorAll('.slide').length")
        print(f"Total slides: {total}")

        tmp = pathlib.Path(tempfile.mkdtemp())
        pdf_pages = []

        for i in range(total):
            page.evaluate(f"show({i})")
            page.wait_for_timeout(300)
            pdf_path = tmp / f"slide_{i:02d}.pdf"
            page.pdf(
                path=str(pdf_path),
                width="1920px",
                height="1080px",
                print_background=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
            pdf_pages.append(pdf_path)
            print(f"  Slide {i+1}/{total} captured")

        browser.close()

    # Merge PDFs
    from pypdf import PdfWriter, PdfReader

    writer = PdfWriter()
    for p in pdf_pages:
        reader = PdfReader(str(p))
        for pg in reader.pages:
            writer.add_page(pg)
    with open(str(out_pdf), "wb") as f:
        writer.write(f)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nPDF saved: {out_pdf}")

if __name__ == "__main__":
    main()
