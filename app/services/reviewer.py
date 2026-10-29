"""LLM 呼叫層：以「強制 tool use」取得結構化 JSON，再用 Pydantic 驗證。"""
import asyncio
import logging

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.schemas import CoverageOutput, FindingsOutput

log = logging.getLogger(__name__)


class LLMOutputError(Exception):
    pass


def inline_refs(schema: dict) -> dict:
    """把 Pydantic 產生的 $defs/$ref 展開成扁平 JSON Schema，提高相容性。"""
    defs = schema.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                target = resolve(defs[node["$ref"].split("/")[-1]])
                return {**target, **{k: resolve(v) for k, v in node.items() if k != "$ref"}}
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(x) for x in node]
        return node

    return resolve(schema)


class LLMReviewer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key or None)
        self._sem = asyncio.Semaphore(settings.llm_concurrency)

    async def _call_tool(self, system: str, user_content: str, tool_name: str, description: str,
                         model_cls: type[BaseModel]) -> BaseModel:
        tool = {"name": tool_name, "description": description,
                "input_schema": inline_refs(model_cls.model_json_schema())}
        messages = [{"role": "user", "content": user_content}]

        for attempt in range(2):
            async with self._sem:
                resp = await self.client.messages.create(
                    model=self.settings.llm_model,
                    max_tokens=self.settings.llm_max_tokens,
                    # system prompt 每次呼叫都相同 → 開啟 prompt caching 省成本
                    system=[{"type": "text", "text": system,
                             "cache_control": {"type": "ephemeral"}}],
                    tools=[tool],
                    tool_choice={"type": "tool", "name": tool_name},
                    messages=messages,
                )
            block = next((b for b in resp.content if b.type == "tool_use"), None)
            if block is None:
                raise LLMOutputError(f"模型未有呼叫 {tool_name}（stop_reason={resp.stop_reason}）")
            try:
                return model_cls.model_validate(block.input)
            except ValidationError as exc:
                log.warning("第 %d 次輸出驗證失敗：%s", attempt + 1, exc)
                # 把錯誤回饋給模型重試一次
                messages = messages + [
                    {"role": "assistant", "content": resp.content},
                    {"role": "user", "content": [{
                        "type": "tool_result", "tool_use_id": block.id, "is_error": True,
                        "content": f"輸出不符合 schema，請修正後重新呼叫 {tool_name}：{exc}",
                    }]},
                ]
        raise LLMOutputError(f"{tool_name} 輸出兩次驗證失敗")

    async def assess_coverage(self, system: str, prompt: str, pairs=None) -> CoverageOutput:
        return await self._call_tool(system, prompt, "submit_coverage", "提交課程重點覆蓋判斷結果", CoverageOutput)

    async def review_accuracy(self, system: str, prompt: str, chunks=None) -> FindingsOutput:
        return await self._call_tool(system, prompt, "submit_findings", "提交教材審查問題清單", FindingsOutput)
