"""AccountSuggestionService（AIService，開發文件 4.5）：在分類規則都沒命中時，建議管理類別。

AI 建議永遠不直接入帳，只放進待確認讓會計採用。回傳一定包含 confidence 與 model_version。
- MockSuggestionService：依關鍵字回傳固定結果
- ClaudeSuggestionService：Anthropic Messages API；失敗或低信心時回傳 None（fallback：只列候選讓會計選）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from app.config import get_settings


@dataclass
class Suggestion:
    category_code: str
    confidence: float
    reason: str
    model_version: str
    alternatives: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"category_code": self.category_code, "confidence": round(self.confidence, 3),
                "reason": self.reason, "model_version": self.model_version, "alternatives": self.alternatives}


class SuggestionService(Protocol):
    def suggest(self, counterparty_name: str, description: str, amount: int,
                candidates: list[dict]) -> Suggestion | None: ...


class MockSuggestionService:
    RULES = [("漂白", "E02", 0.86, "品名是清潔耗材"), ("垃圾袋", "E02", 0.86, "品名是清潔耗材"),
             ("文具", "N04", 0.55, "辦公用品，類別不明確"), ("利息", "N01", 0.95, "摘要為利息")]

    def suggest(self, counterparty_name: str, description: str, amount: int, candidates: list[dict]) -> Suggestion | None:
        text = f"{counterparty_name} {description}"
        allowed = {c["category_code"] for c in candidates}
        for kw, code, conf, reason in self.RULES:
            if kw in text and code in allowed:
                return Suggestion(code, conf, reason, "mock@v1")
        return None


PROMPT = """你是台灣餐飲集團的會計助理。請依廠商名稱與摘要，從候選類別中選一個最適合的管理類別。
只能從候選清單中選。回傳 JSON：{"category_code": "...", "confidence": 0到1, "reason": "一句中文理由",
"alternatives": [{"category_code": "...", "confidence": 0到1}]}，不要輸出其他文字。"""


class ClaudeSuggestionService:
    def __init__(self) -> None:
        import anthropic  # 只有正式環境需要

        s = get_settings()
        self._client = anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=s.ai_timeout_sec)
        self._model = s.ai_model
        self._version = f"{s.ai_model}@{s.ai_prompt_version}"

    def suggest(self, counterparty_name: str, description: str, amount: int, candidates: list[dict]) -> Suggestion | None:
        user = json.dumps({"counterparty_name": counterparty_name, "description": description, "amount": amount,
                           "candidates": candidates}, ensure_ascii=False)
        try:
            msg = self._client.messages.create(model=self._model, max_tokens=300, system=PROMPT,
                                               messages=[{"role": "user", "content": user}])
            data = json.loads(msg.content[0].text)
            allowed = {c["category_code"] for c in candidates}
            if data.get("category_code") not in allowed:
                return None
            return Suggestion(data["category_code"], float(data.get("confidence", 0)), data.get("reason", ""),
                              self._version, data.get("alternatives", []))
        except Exception:  # noqa: BLE001 — 任何失敗都走 fallback
            return None


_svc = None


def get_suggestion_service():
    global _svc
    if _svc is None:
        s = get_settings()
        _svc = MockSuggestionService() if s.use_mocks or not s.anthropic_api_key else ClaudeSuggestionService()
    return _svc


def suggest_if_confident(counterparty_name: str, description: str, amount: int, candidates: list[dict]) -> dict | None:
    sug = get_suggestion_service().suggest(counterparty_name, description, amount, candidates)
    if sug is None or sug.confidence < get_settings().ai_min_confidence:
        return None
    return sug.to_dict()
