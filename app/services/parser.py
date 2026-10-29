"""文件解析：PDF / TXT → 逐頁純文字。"""
import re
from dataclasses import dataclass


class ParseError(Exception):
    pass


@dataclass
class Page:
    page_no: int | None
    text: str


_SENTENCE_END = "。！？；：」』）!?;:)"
_CJK = r"\u3000-\u303f\u3400-\u9fff\uff00-\uffef"
_CJK_SPACES = re.compile(rf"(?<=[{_CJK}])[ \t\u00a0]+(?=[{_CJK}])")


def collapse_cjk_spaces(text: str) -> str:
    """部分 PDF 會在每個中文字之間插入空格（例如「科 學 實 踐」），會破壞檢索及引文核對。"""
    return _CJK_SPACES.sub("", text)


def _join_broken_lines(text: str) -> str:
    """中文 PDF 常在句子中途換行。若一行不是以句末標點結束，就與下一行合併；空行保留作段落分隔。"""
    out: list[str] = []
    for line in text.splitlines():
        line = collapse_cjk_spaces(line.strip())
        if not line:
            out.append("\n\n")
            continue
        if out and out[-1] not in ("\n\n",) and out[-1][-1:] not in _SENTENCE_END:
            out[-1] += line
        else:
            out.append(line)
    joined = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", joined).strip()


def _decode_text(data: bytes) -> str:
    # 香港學校的舊檔案常為 Big5-HKSCS，內地來源可能是 GB18030
    for enc in ("utf-8-sig", "big5hkscs", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ParseError("無法辨認文字檔編碼，請另存為 UTF-8")


def parse_document(filename: str, data: bytes) -> list[Page]:
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        import pymupdf  # 延遲載入方便單元測試

        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:  # noqa: BLE001
            raise ParseError(f"PDF 無法開啟：{exc}") from exc
        pages = [Page(i + 1, _join_broken_lines(p.get_text("text"))) for i, p in enumerate(doc)]
        total = sum(len(p.text) for p in pages)
        if pages and total < 30 * len(pages):
            raise ParseError("PDF 幾乎沒有可擷取文字，可能是掃描檔；MVP 暫不支援 OCR")
        return [p for p in pages if p.text]
    if name.endswith((".txt", ".md")):
        return [Page(None, _join_broken_lines(_decode_text(data)))]
    raise ParseError("只支援 PDF、TXT 或 MD 檔")
