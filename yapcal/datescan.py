"""Scan documents (syllabi, emails) for date-bearing lines.

Strategy: regex finds candidate lines cheaply; the LLM only *labels* them.
Small models can't read a whole syllabus reliably, but they can name an
event from a 15-line window.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

MONTH_RE = r"(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)"
DATE_PATTERNS = [
    re.compile(rf"\b{MONTH_RE}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b", re.I),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH_RE}\b", re.I),
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I),
    re.compile(r"\bfinals?\b|\bmidterm\b|\bexam\b|\bquiz\b|\bexams?\b", re.I),
]
EVENT_HINTS = re.compile(
    r"\b(due|deadline|exam|midterm|final|quiz|test|presentation|project|report|"
    r"assignment|homework|lab|lecture|meeting|office hours|holiday|break|"
    r"vacation|conference|workshop|read|reading|submission|opens|closes)\b", re.I)


def scan(text: str) -> list[dict]:
    """Return windows: [{lines: str, hits: [str]}] — merged, deduped."""
    lines = text.splitlines()
    marked = []
    for i, ln in enumerate(lines):
        hits = []
        for pat in DATE_PATTERNS:
            m = pat.search(ln)
            if m:
                hits.append(m.group(0))
        if hits and (EVENT_HINTS.search(ln) or len(hits) > 1):
            marked.append((i, hits))
    if not marked:
        # second pass: date lines without event words (e.g. calendar tables)
        for i, ln in enumerate(lines):
            for pat in DATE_PATTERNS[:4]:
                if pat.search(ln):
                    marked.append((i, [pat.search(ln).group(0)]))
                    break
    marked.sort()
    windows = []
    used = set()
    for i, hits in marked:
        if any(j in used for j in range(max(0, i - 2), i + 1)):
            # extend previous window if overlapping
            if windows and i <= windows[-1]["end"] + 2:
                windows[-1]["end"] = i
                windows[-1]["hits"] += hits
                used.update(range(windows[-1]["end"], i + 1))
                continue
        start = max(0, i - 2)
        end = min(len(lines) - 1, i + 2)
        windows.append({"start": start, "end": end, "hits": list(hits)})
        used.update(range(start, end + 1))
    for w in windows:
        w["lines"] = "\n".join(lines[w["start"]: w["end"] + 1])
    return windows


def load_document(path: str) -> str:
    """Read txt/md/eml/csv directly; pdf via pdftotext if installed; docx natively."""
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(path)
    ext = p.suffix.lower()
    if ext in (".txt", ".md", ".eml", ".csv", ".text", ""):
        return p.read_text(errors="replace")
    if ext == ".docx":
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml").decode(errors="replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml)
    if ext == ".pdf":
        import shutil
        import subprocess
        if shutil.which("pdftotext"):
            return subprocess.run(["pdftotext", "-layout", str(p), "-"],
                                  capture_output=True, text=True, timeout=60).stdout
        raise RuntimeError(
            "PDF support needs 'pdftotext' (poppler-utils), or paste the text "
            "into a .txt file and ingest that instead.")
    raise RuntimeError(f"unsupported file type: {ext}")
