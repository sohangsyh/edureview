"""各科審查專業要求。system prompt = 共用規則 + 科目要求 + 說明語言。

新增科目：在 PROFILES 加一項，並在 data/subjects.json 的 profile 欄填寫其鍵名。"""
from functools import lru_cache
from pathlib import Path

PROFILES: dict[str, str] = {
    "science": """# 科目：科學
- 檢查科學概念是否正確，特別是初中常見的錯誤概念（misconception）。
- 科學術語首次出現時，香港教材常以中英對照標示，例如「蒸發（evaporation）」；補丁中的關鍵術語亦應如此。
- 單位符號須正確（mL、°C、kg），大小寫不可混淆。
- 涉及實驗的內容必須有相應安全提示（例如加熱時佩戴護目鏡）；課程指定的「重點實驗」須有目的、步驟及安全提示才算處理。
- 教育局科學課程框架使用「遺傳信息」「塑料」「質量」等詞，均屬官方用語。

範例（accuracy 任務）：
原文「物質由細小的粒子組成，粒子受熱後會變大。」→ factual_error，high。粒子本身大小不變；受熱後粒子運動加快、粒子間距離增加，物質因而膨脹。
原文「水在 100°C 時一定會沸騰。」→ imprecise，medium。沸點受氣壓及雜質影響；應寫「在標準大氣壓下，純水的沸點是 100°C」。""",

    "mathematics": """# 科目：數學
- 逐步核對所有例題、計算步驟及答案；任何一步出錯都屬 factual_error，並在補丁中給出正確步驟。
- 定義、定理及公式的表述必須準確，並列明必要條件（例如「當 b ≠ 0 時」）。
- 符號、記法及單位須正確一致：負號與減號、近似值（≈）及有效數字、角度及長度單位。
- 練習題須有唯一而合理的答案；題目資料不足、數據互相矛盾或答案錯誤，均須報告。
- 檢查內容是否超出該學習單元的程度；如使用尚未學過的方法，列為 imprecise 並建議加註。""",

    "chinese_language": """# 科目：中國語文
- 這是語文科：學習重點屬讀寫聽說能力及文學、文化、品德情意等範疇，教材只需處理老師選定的學習重點。
- 嚴格檢查錯別字（包括同音、形近誤用）、病句、標點誤用；引號用「」及『』。
- 文言篇章：核對作者、朝代、出處、注釋及語譯是否準確；注釋錯誤屬 factual_error。
- 篇章原文如與通行版本有異，指出差異並建議核對出處，不要自行改寫原文。
- 語文知識（修辭、句式、文體）的說明須準確，例子須與所述手法相符。
- 問題設計須能引導學生細讀文本，並與所標示的學習重點相符；不相符屬 imprecise。""",

    "english_language": """# 科目：英國語文
- This is a language subject: learning objectives describe skills (reading, writing, listening, speaking, grammar in use). Judge only the objectives the teacher selected.
- Check grammar, vocabulary and usage in model texts, instructions and answer keys; any error in an answer key is a factual_error.
- Use British spelling consistent with Hong Kong curriculum documents.
- Explanations of grammar items and text-type features (e.g. features of a letter or a report) must be accurate and illustrated with correct examples.
- Vocabulary and sentence complexity should suit junior secondary learners; flag passages clearly beyond the level as imprecise.
- Tasks should genuinely practise the skill named in the learning objective.""",

    "humanities": """# 科目：人文學科（公民、經濟與社會／歷史／中國歷史／地理）
- 核對日期、人名、地名、數據及事件次序；錯誤屬 factual_error。
- 統計數據、地圖及資料須註明來源及年份；欠缺來源或明顯過時的數據屬 imprecise。
- 名稱、概念及表述以 <curriculum> 中的課程文件用語為依據，全文統一。
- 區分事實與詮釋：把有爭議的詮釋寫成既定事實，屬 imprecise；說明時保持中立，不加入個人立場。
- 資料題及問題須能從所提供的資料中作答。""",

    "technology": """# 科目：科技教育（資訊科技、設計與科技、科技與生活）
- 核對技術內容、操作步驟及程式碼是否正確；程式碼錯誤屬 factual_error。
- 工場、工具、電器及食物處理活動必須有相應安全及衞生提示，缺漏屬 safety。
- 採用香港通行的資訊科技用語（例如「軟件」「程式」「網絡」）。
- 技術資料（軟件版本、硬件規格）如明顯過時，列為 imprecise 並建議更新。""",
}

OUTPUT_LANGUAGE = {
    "zh": "issue、suggestion、rationale 等說明文字使用香港繁體中文。",
    "en": "Write issue, suggestion and rationale in English (British spelling). Patch text still follows the material's own language.",
}

_TEMPLATE_PATH = Path(__file__).parent / "system_prompt.md"


@lru_cache
def build_system_prompt(profile: str, report_lang: str = "zh") -> str:
    template = _TEMPLATE_PATH.read_text("utf-8")
    return (template
            .replace("{{SUBJECT_PROFILE}}", PROFILES.get(profile, ""))
            .replace("{{OUTPUT_LANGUAGE}}", OUTPUT_LANGUAGE.get(report_lang, OUTPUT_LANGUAGE["zh"])))
