"""以「段落 → 句子」為單位切塊，保留頁碼，方便報告定位。"""
import re

from app.schemas import Chunk
from app.services.parser import Page

_SENT_SPLIT = re.compile(r"(?<=[。！？；!?;])")


def _sentences(paragraph: str) -> list[str]:
    return [s for s in _SENT_SPLIT.split(paragraph) if s.strip()]


def chunk_pages(pages: list[Page], size: int = 700, overlap: int = 100) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page in pages:
        prefix = f"p{page.page_no}" if page.page_no is not None else "t"
        buf = ""
        n = 0

        def emit(text: str) -> None:
            nonlocal n
            n += 1
            chunks.append(Chunk(chunk_id=f"{prefix}-c{n}", page=page.page_no, text=text.strip()))

        for para in page.text.split("\n\n"):
            for sent in _sentences(para.strip()) or [para.strip()]:
                if buf and len(buf) + len(sent) > size:
                    emit(buf)
                    buf = buf[-overlap:] if overlap else ""
                buf += sent
            buf += "\n"
        if buf.strip():
            emit(buf)
    return chunks
