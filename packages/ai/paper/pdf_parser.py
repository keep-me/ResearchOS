from __future__ import annotations

from pathlib import Path


class PdfTextExtractor:
    """
    Optional text extractor for deep-dive fallback.
    - If PyMuPDF is installed, extracts text from the first N pages.
    - Otherwise returns a lightweight stub description.
    """

    def extract_text(self, pdf_path: str, max_pages: int = 12) -> str:
        path = Path(pdf_path)
        if not path.exists():
            return ""
        try:
            import fitz  # type: ignore

            doc = fitz.open(pdf_path)
            chunks: list[str] = []
            page_count = len(doc)
            page_limit = page_count if int(max_pages) <= 0 else min(int(max_pages), page_count)
            unbounded = int(max_pages) <= 0
            for i in range(page_limit):
                text = doc.load_page(i).get_text("text").strip()
                if text:
                    chunks.append(text if unbounded else text[:2000])
            doc.close()
            joined = "\n\n".join(chunks)
            return joined if unbounded else joined[:12000]
        except Exception:
            return f"PDF text extraction fallback for {path.name}; parser unavailable."
