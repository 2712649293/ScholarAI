"""Markdown section/block boundaries + adjacent embedding semantic distance."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Callable

from markdown_it import MarkdownIt

from app.config import settings
from app.rag.pdf_loader import PageText
from app.rag.tokens import embedding_counter, split_to_limit


@dataclass
class Block:
    text: str
    section: str
    spans: list[dict]
    kind: str = "paragraph"
    figures: list[str] = field(default_factory=list)


@dataclass
class SemanticChunk:
    text: str
    page: int
    page_end: int
    chunk_index: int
    section: str
    source_spans: list[dict]
    figure_ids: list[str]
    block_type: str
    token_count: int


def markdown_blocks(pages: list[PageText]) -> list[Block]:
    parser = MarkdownIt("commonmark").enable("table")
    sections: list[str] = []
    blocks: list[Block] = []
    for page in pages:
        lines = page.text.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        for token in parser.parse(page.text):
            if token.level != 0 or token.map is None or token.type.endswith("_close"):
                continue
            start, end = token.map
            text = "".join(lines[start:end]).strip()
            if not text:
                continue
            if token.type == "heading_open":
                depth = int(token.tag[1:])
                sections = sections[:depth - 1]
                sections.append(re.sub(r"^#+\s*", "", text).strip(" #"))
                continue
            span = {"page": page.page, "line_start": start + 1, "line_end": end,
                    "char_start": offsets[start], "char_end": offsets[end]}
            # Group figure marker, image, generated description and author caption.
            marker = re.search(r"<!-- figure:([^ >]+) -->", text)
            if marker:
                blocks.append(Block(text, " / ".join(sections), [span], "figure", [marker[1]]))
            elif blocks and blocks[-1].kind == "figure" and blocks[-1].spans[-1]["page"] == page.page and (
                text.startswith("![") or text.startswith("> [AI 图片描述") or
                re.match(r"(?:Figure|Fig\.?|图|Scheme)\s*[\dIVX]+", re.sub(r"[*_]", "", text), re.I)
            ):
                blocks[-1].text += "\n\n" + text
                blocks[-1].spans.append(span)
            else:
                kind = "table" if token.type == "table_open" else "paragraph"
                if token.type in {"fence", "code_block"} or text.startswith("$$"):
                    kind = "formula_or_code"
                blocks.append(Block(text, " / ".join(sections), [span], kind))
    return blocks


def _prefix(section: str) -> str:
    return f"章节：{section}\n\n" if section else ""


def split_markdown(pages: list[PageText], embed: Callable[[list[str]], list[list[float]]]) -> tuple[list[SemanticChunk], list[str]]:
    count, model_limit = embedding_counter()
    limit = min(settings.chunk_max_tokens, model_limit)
    target = min(settings.chunk_target_tokens, limit)
    minimum = min(settings.chunk_min_tokens, target)
    blocks: list[Block] = []
    prefixes: dict[str, str] = {}
    warnings: list[str] = []
    for block in markdown_blocks(pages):
        # Avoid an unusually long title consuming the entire embedding budget.
        section_parts = split_to_limit(block.section, max(8, limit // 4), count) if block.section else [""]
        if len(section_parts) > 1:
            warnings.append("章节标题过长，检索前缀已缩短；完整标题保留在 Markdown")
        prefix = _prefix(section_parts[0])
        prefixes[block.section] = prefix
        budget = limit - count(prefix) - 4
        parts: list[str] = []
        if block.kind == "table" and count(prefix + block.text) > limit:
            rows = block.text.splitlines()
            header = "\n".join(rows[:2]) + "\n"
            if count(header) < budget // 2:
                group = header
                for row in rows[2:]:
                    if count(group + row) > budget and group != header:
                        parts.append(group)
                        group = header
                    if count(header + row) > budget:
                        parts.extend(split_to_limit(header + row, budget, count))
                    else:
                        group += row + "\n"
                if group != header:
                    parts.append(group)
        if not parts:
            parts = split_to_limit(block.text, budget, count)
        if len(parts) > 1 and block.kind in {"figure", "formula_or_code"}:
            warnings.append(f"第 {block.spans[0]['page']} 页 {block.kind} 超过 token 上限，按长度拆分")
        for part in parts:
            blocks.append(Block(part, block.section, block.spans, block.kind, block.figures))
    if not blocks:
        return [], warnings
    distances: dict[int, float] = {}
    try:
        vectors = embed([prefixes[b.section] + b.text for b in blocks])
        if len(vectors) != len(blocks):
            raise ValueError("semantic embedding count mismatch")
        for i in range(1, len(blocks)):
            if blocks[i - 1].section != blocks[i].section:
                continue
            a, b = vectors[i - 1], vectors[i]
            denominator = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
            distances[i] = 1 - sum(x * y for x, y in zip(a, b)) / denominator
    except Exception:
        warnings.append("语义边界计算失败，已降级为章节与长度切分")
    ordered = sorted(distances.values())
    threshold = ordered[min(len(ordered) - 1, math.ceil(len(ordered) * .9) - 1)] if ordered else 1.0
    out: list[SemanticChunk] = []
    current: list[Block] = []

    def render(group: list[Block]) -> str:
        return prefixes[group[0].section] + "\n\n".join(b.text for b in group)

    def flush() -> None:
        if not current:
            return
        spans = [s for b in current for s in b.spans]
        text = render(current)
        out.append(SemanticChunk(text, min(s["page"] for s in spans), max(s["page"] for s in spans),
            len(out), current[0].section, spans, list(dict.fromkeys(f for b in current for f in b.figures)),
            current[0].kind if len({b.kind for b in current}) == 1 else "mixed", count(text)))

    for i, block in enumerate(blocks):
        if current:
            size = count(render(current))
            section_change = current[-1].section != block.section
            semantic_break = size >= minimum and distances.get(i, 0) >= threshold and distances.get(i, 0) > .05
            too_long = count(render(current + [block])) > limit
            if section_change or semantic_break or too_long or size >= target:
                previous = current[-1]
                flush()
                current = []
                # Overlap only a complete final sentence of ordinary prose.
                if not section_change and previous.kind == "paragraph" and settings.chunk_overlap_tokens:
                    sentences = re.findall(r"[^。！？.!?]+[。！？.!?](?:\s+|$)", previous.text)
                    if sentences:
                        sentence = sentences[-1].strip()
                        overlap = Block(sentence, previous.section, previous.spans)
                        if count(sentence) <= settings.chunk_overlap_tokens and count(render([overlap, block])) <= limit:
                            current.append(overlap)
        current.append(block)
    flush()
    if any(c.token_count > limit for c in out):
        raise ValueError("chunk exceeds embedding token limit")
    return out, warnings
