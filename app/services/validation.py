"""防幻覺檢查：引文是否真的存在於教材。"""
import unicodedata


def normalize_for_match(text: str) -> str:
    # NFKC 統一全形/半形標點與數字，再去除所有空白
    text = unicodedata.normalize("NFKC", text)
    return "".join(text.split())


def quote_in_text(quote: str, normalized_full_text: str) -> bool:
    q = normalize_for_match(quote)
    return bool(q) and q in normalized_full_text
