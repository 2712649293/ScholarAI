"""Layout-aware PDF extraction; OCR is deliberately disabled."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from threading import RLock
from typing import Any

import pymupdf
import pymupdf4llm

from app.config import settings
from app.errors import ResourceLimitExceeded
from app.rag.pdf_loader import PageText

_parser_lock = RLock()  # PyMuPDF/layout must not run concurrently in threads.


@dataclass
class Figure:
    figure_id: str
    page: int
    path: str
    bbox: list[float]
    caption: str
    context: str
    status: str = "skipped"
    description: str = ""
    model: str = ""
    description_source_kind: str = "generated_image_description"
    prompt_version: str = ""
    description_created_at: float | None = None


@dataclass
class ParsedPDF:
    pages: list[PageText]
    figures: list[Figure] = field(default_factory=list)
    layout: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extracted_pages: list[PageText] = field(default_factory=list)

    def save(self, directory: Path, *, enriched: bool = False) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        name = "paper.enriched.md" if enriched else "paper.raw.md"
        (directory / name).write_text("\n\n".join(
            f"<!-- page:{p.page} -->\n{p.text}" for p in self.pages
        ), encoding="utf-8")
        if enriched:
            from dataclasses import asdict
            (directory / "manifest.json").write_text(json.dumps({
                "parser": "pymupdf4llm-1.28.2", "ocr": False,
                "pages": self.layout, "figures": [asdict(f) for f in self.figures],
                "warnings": self.warnings,
                "layout_positions_reference": "paper.extracted.md (page-local character offsets)",
                "chunk_positions_reference": "paper.enriched.md (page-local lines/characters)",
            }, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_pdf(pdf_path: str | Path, directory: Path) -> ParsedPDF:
    with _parser_lock:
        return _parse_pdf(pdf_path, directory)


def _parse_pdf(pdf_path: str | Path, directory: Path) -> ParsedPDF:
    images = directory / "images"
    images.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(str(pdf_path)) as pdf:
        if pdf.page_count > settings.upload_max_pages:
            raise ResourceLimitExceeded(f"PDF 超过 {settings.upload_max_pages} 页上限")
        extracted = pymupdf4llm.to_markdown(
            pdf, page_chunks=True, write_images=True, image_path=str(images.resolve()),
            use_ocr=False, force_ocr=False, header=True, footer=True,
        )
    parsed = ParsedPDF([])
    for item in extracted:
        page = int(item["metadata"]["page_number"])
        raw = item["text"]
        parsed.extracted_pages.append(PageText(page, raw))
        boxes = item.get("page_boxes", [])
        pictures = [box for box in boxes if box.get("class") == "picture"]
        figure_index = 0

        def replace_image(match: re.Match[str]) -> str:
            nonlocal figure_index
            figure_index += 1
            fid = f"p{page}-fig{figure_index}"
            asset = Path(match.group(1)).resolve()
            if not asset.is_relative_to(images.resolve()) or not asset.is_file():
                parsed.warnings.append(f"{fid}: image asset missing")
                return match.group(0)
            tail = raw[match.end():match.end() + 1000].strip()
            candidate = tail.split("\n\n", 1)[0] if tail else ""
            caption = candidate if re.match(r"(?:Figure|Fig\.?|图|Scheme)\s*\d+", re.sub(r"[*_]", "", candidate), re.I) else ""
            box = pictures[figure_index - 1] if figure_index <= len(pictures) else {}
            context = raw[max(0, match.start() - 600):match.start()] + "\n" + tail[:600]
            # Extraction paths include the new generation directory. They must
            # not invalidate otherwise identical neighboring-context cache keys.
            context = re.sub(r"!\[[^\]]*\]\([^\n)]+\)", "[论文图片]", context)
            parsed.figures.append(Figure(
                fid, page, str(asset), list(box.get("bbox", [])), caption,
                context,
            ))
            return f"<!-- figure:{fid} -->\n![{fid}](images/{asset.name})"

        text = re.sub(r"!\[[^\]]*\]\(([^\n)]+)\)", replace_image, raw)
        # Only repair letter-to-letter line-end hyphenation, never model names.
        text = re.sub(r"(?<=[a-z])-[ \t]*\n(?=[a-z])", "", text)
        parsed.pages.append(PageText(page, text))
        parsed.layout.append({"page": page, "boxes": boxes})
        if not raw.strip():
            parsed.warnings.append(f"第 {page} 页没有文本；本版本关闭 OCR")
    parsed.save(directory)
    (directory / "paper.extracted.md").write_text("\n\n".join(
        f"<!-- page:{p.page} -->\n{p.text}" for p in parsed.extracted_pages
    ), encoding="utf-8")
    return parsed
