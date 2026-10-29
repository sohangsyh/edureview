"""判斷教材語言。中英夾雜時以中文字比例決定。"""
import re

_CJK = re.compile(r"[\u3400-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")


def detect_language(text: str) -> str:
    cjk = len(_CJK.findall(text))
    # 一個英文詞約等於 5 個字母，換算成「詞」再與中文字比較
    latin_words = len(_LATIN.findall(text)) / 5
    if cjk == 0 and latin_words == 0:
        return "zh"
    return "zh" if cjk >= latin_words * 0.5 else "en"
