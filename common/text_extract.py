from __future__ import annotations
import os
from pathlib import Path
from typing import Optional


def _read_text_file(p: Path) -> str:
    try:
        return p.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        try:
            return p.read_text(encoding='cp1251', errors='ignore')
        except Exception:
            return ''


def _read_pdf(p: Path) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(p))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or '')
            except Exception:
                continue
        return '\n'.join([t for t in parts if t]).strip()
    except Exception:
        return ''


def _read_docx(p: Path) -> str:
    try:
        import docx  # python-docx
        doc = docx.Document(str(p))
        parts = []
        for para in doc.paragraphs:
            parts.append(para.text or '')
        return '\n'.join(parts).strip()
    except Exception:
        return ''


def extract_text_from_file(path: Path, mime_type: Optional[str] = None) -> str:
    """Best-effort text extraction for CV files (pdf, docx, txt)."""
    if not path or not Path(path).exists():
        return ''
    p = Path(path)
    mt = (mime_type or '').lower()
    ext = p.suffix.lower()

    # Prefer MIME when available
    if 'pdf' in mt or ext == '.pdf':
        text = _read_pdf(p)
        if text:
            return text
    if 'word' in mt or ext in ('.docx',):
        text = _read_docx(p)
        if text:
            return text
    if 'text' in mt or ext in ('.txt', '.md'):
        text = _read_text_file(p)
        if text:
            return text

    # Fallback tries
    text = _read_text_file(p)
    if text:
        return text
    text = _read_pdf(p)
    if text:
        return text
    text = _read_docx(p)
    return text

