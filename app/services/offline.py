"""離線示範模式：不需 API key、不需下載 embedding 模型，方便試用網站及開發前端。

- CharNgramEmbedding：以字元二元組雜湊作向量，只供示範，檢索質素遠低於真正模型。
- OfflineReviewer：以關鍵詞比對判斷覆蓋情況，不做準確性審查。
字形檢查與用語檢查屬規則引擎，在示範模式下照常運作。
"""
import hashlib

import numpy as np
from chromadb.api.types import EmbeddingFunction

from app.schemas import CoverageItem, CoverageOutput, CoverageStatus, FindingsOutput


class CharNgramEmbedding(EmbeddingFunction):
    def __init__(self, dim: int = 512):
        self.dim = dim

    def __call__(self, input):
        out = []
        for text in input:
            v = np.zeros(self.dim, dtype=np.float32)
            for i in range(len(text) - 1):
                v[int(hashlib.md5(text[i:i + 2].encode()).hexdigest(), 16) % self.dim] += 1
            out.append(v / (np.linalg.norm(v) + 1e-9))
        return out

    @staticmethod
    def name() -> str:
        return "char-ngram-demo"

    def get_config(self):
        return {"dim": self.dim}

    @staticmethod
    def build_from_config(config):
        return CharNgramEmbedding(**config)


class OfflineReviewer:
    async def assess_coverage(self, system, prompt, pairs) -> CoverageOutput:
        items = []
        for objective, chunks in pairs:
            text = "".join(c.text for c in chunks)
            terms = objective.key_terms or []
            found = [t for t in terms if t in text]
            if terms and len(found) == len(terms):
                status = CoverageStatus.covered
            elif found:
                status = CoverageStatus.partial
            else:
                status = CoverageStatus.missing
            items.append(CoverageItem(
                objective_id=objective.id, status=status,
                evidence_chunk_ids=[c.chunk_id for c in chunks if any(t in c.text for t in found)],
                rationale=(f"Demo mode, keyword match: {len(found)}/{len(terms)} key terms found"
                           f"{' (' + ', '.join(found) + ')' if found else ''}."
                           if "Write issue, suggestion and rationale in English" in system else
                           f"示範模式以關鍵詞比對判斷：找到 {len(found)}／{len(terms)} 個關鍵詞"
                           f"{'（' + '、'.join(found) + '）' if found else ''}。")))
        return CoverageOutput(items=items)

    async def review_accuracy(self, system, prompt, chunks) -> FindingsOutput:
        return FindingsOutput(findings=[])
