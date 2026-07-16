"""PDF 文本抽取：pdfplumber 为主，pymupdf 兜底（扫描页/异常）。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pdfplumber


@dataclass
class PageText:
    page: int  # 1-indexed
    text: str


def load_pdf(pdf_path: str | Path) -> list[PageText]:
    """抽每页文本。失败抛 PDFParseFailed。"""
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")
    pages: list[PageText] = []
    try:
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                pages.append(PageText(page=i, text=text))
    except Exception as e:  # pdfplumber 解析失败时兜底到 pymupdf
        try:
            import pymupdf
            doc = pymupdf.open(str(path))
            for i, page in enumerate(doc, start=1):
                text = page.get_text() or ""
                pages.append(PageText(page=i, text=text))
            doc.close()
        except Exception as e2:
            raise RuntimeError(f"PDF 解析失败：{e}; 兜底也失败：{e2}") from e2
    return pages
