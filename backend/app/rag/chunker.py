"""文本分块：langchain RecursiveCharacterTextSplitter，按段落+句子切。"""
from __future__ import annotations

from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.pdf_loader import PageText


@dataclass
class Chunk:
    text: str
    page: int  # 该 chunk 来自哪一页
    chunk_index: int  # 同一页内的块序号


def split_pages(pages: list[PageText], *, chunk_size: int = 800, overlap: int = 150) -> list[Chunk]:
    """把每页文本切块，保留页码信息。"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""],
    )
    out: list[Chunk] = []
    for p in pages:
        if not p.text.strip():
            continue
        pieces = splitter.split_text(p.text)
        for i, piece in enumerate(pieces):
            out.append(Chunk(text=piece, page=p.page, chunk_index=i))
    return out
