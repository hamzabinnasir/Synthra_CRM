"""Turn an uploaded file into plain text for the knowledge base."""
from __future__ import annotations

import io
import re
from pathlib import PurePath

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".rst"}
MAX_CHARS = 1_000_000
MAX_PDF_PAGES = 300


class UnsupportedDocument(ValueError):
    """The file cannot be read. The message is safe to show to the user."""


def clean_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(filename: str, data: bytes) -> str:
    suffix = PurePath(filename or "").suffix.lower()
    if suffix in TEXT_SUFFIXES:
        text = data.decode("utf-8-sig", errors="replace")
    elif suffix == ".pdf":
        text = _pdf_text(data)
    else:
        raise UnsupportedDocument("Unsupported file type. Upload .txt, .md, .csv, .json or .pdf files.")
    text = clean_text(text)
    if not text:
        raise UnsupportedDocument("No readable text was found in that file (scanned PDFs need OCR first).")
    if len(text) > MAX_CHARS:
        raise UnsupportedDocument(f"That document is too long (limit {MAX_CHARS:,} characters). Split it into parts.")
    return text


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise UnsupportedDocument("That PDF is password protected.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise UnsupportedDocument(f"That PDF has too many pages (limit {MAX_PDF_PAGES}).")
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except UnsupportedDocument:
        raise
    except Exception:  # corrupt file: never leak parser internals
        raise UnsupportedDocument("That PDF could not be read. It may be damaged.")
