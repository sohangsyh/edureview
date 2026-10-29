"""把老師接受的補丁套用到教材文字，產生修訂版。只做逐字匹配，找不到錨點的補丁會列出，交老師手動處理。"""
from dataclasses import dataclass, field


@dataclass
class PatchResult:
    text: str
    applied: list[str] = field(default_factory=list)
    unapplied: list[str] = field(default_factory=list)


def apply_patches(material: str, findings: list[dict]) -> PatchResult:
    text = material
    result = PatchResult(text="")
    appendix: list[str] = []
    for f in findings:
        patch = f.get("patch")
        if not patch:
            result.unapplied.append(f["finding_id"])
            continue
        action, anchor, new = patch["action"], patch.get("anchor_text", ""), patch.get("new_text", "")
        if not anchor:
            if action == "insert_after" and new:
                appendix.append(new)
                result.applied.append(f["finding_id"])
            else:
                result.unapplied.append(f["finding_id"])
            continue
        pos = text.find(anchor)
        if pos < 0:
            result.unapplied.append(f["finding_id"])
            continue
        end = pos + len(anchor)
        if action == "replace":
            text = text[:pos] + new + text[end:]
        elif action == "delete":
            text = text[:pos] + text[end:]
        else:  # insert_after
            text = text[:end] + new + text[end:]
        result.applied.append(f["finding_id"])
    if appendix:
        text += "\n\n【補充內容：以下為審查建議新增的課程內容，請移至合適位置】\n\n" + "\n\n".join(appendix)
    result.text = text
    return result
