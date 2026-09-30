"""LangChain-backed chat and embedding adapters (init_chat_model / init_embeddings)."""

from __future__ import annotations

from typing import Any

import numpy as np
from langchain_core.messages import HumanMessage, SystemMessage

from dreampet.config import RoleConfig
from dreampet.providers.base import (
    ChatRequest,
    ChatResult,
    ProviderError,
    StructResult,
    Usage,
    estimate_tokens,
)


def _usage_from(msg: Any, req: ChatRequest, out_text: str) -> Usage:
    meta = getattr(msg, "usage_metadata", None) or {}
    tin = int(meta.get("input_tokens") or estimate_tokens(req.system + req.user))
    tout = int(meta.get("output_tokens") or estimate_tokens(out_text))
    return Usage(tokens_in=tin, tokens_out=tout)


class LangChainChat:
    def __init__(self, rc: RoleConfig, callbacks: list | None = None):
        from langchain.chat_models import init_chat_model

        kwargs: dict[str, Any] = {}
        if rc.api_key:
            kwargs["api_key"] = rc.api_key
        if rc.url:
            kwargs["base_url"] = rc.url
        if rc.temperature is not None:
            kwargs["temperature"] = rc.temperature
        kwargs.update(rc.extra_options())
        if not rc.model:
            raise ProviderError(f"role using provider {rc.provider!r} needs a model name")
        try:
            self.model = init_chat_model(rc.model, model_provider=rc.provider, **kwargs)
        except ImportError as exc:
            raise ProviderError(f"{rc.provider} support is not installed: {exc}. Try `uv sync --extra cloud`.") from exc
        self.model_name = f"{rc.provider}:{rc.model}"
        self.callbacks = callbacks or []

    def _messages(self, req: ChatRequest):
        return [SystemMessage(req.system), HumanMessage(req.user)]

    def _model(self, req: ChatRequest):
        m = self.model
        bind: dict[str, Any] = {}
        if req.temperature is not None:
            bind["temperature"] = req.temperature
        if req.max_tokens:
            bind["max_tokens"] = req.max_tokens
        return m.bind(**bind) if bind else m

    def generate(self, req: ChatRequest) -> ChatResult:
        try:
            msg = self._model(req).invoke(self._messages(req), config={"callbacks": self.callbacks, "run_name": req.task})
        except Exception as exc:
            raise ProviderError(f"{self.model_name}: {exc}") from exc
        text = msg.content if isinstance(msg.content, str) else "".join(
            p.get("text", "") for p in msg.content if isinstance(p, dict))
        return ChatResult(text=text.strip(), usage=_usage_from(msg, req, text))

    def structured(self, req: ChatRequest, schema) -> StructResult:
        try:
            runnable = self.model.with_structured_output(schema, include_raw=True)
            out = runnable.invoke(self._messages(req), config={"callbacks": self.callbacks, "run_name": req.task})
        except Exception as exc:
            raise ProviderError(f"{self.model_name}: {exc}") from exc
        parsed = out.get("parsed")
        if parsed is None:
            raise ProviderError(f"{self.model_name}: could not parse {schema.__name__}: {out.get('parsing_error')}")
        if isinstance(parsed, dict):
            parsed = schema.model_validate(parsed)
        return StructResult(data=parsed, usage=_usage_from(out.get("raw"), req, parsed.model_dump_json()))


class LangChainEmbeddings:
    def __init__(self, rc: RoleConfig):
        from langchain.embeddings import init_embeddings

        kwargs: dict[str, Any] = {}
        if rc.api_key:
            kwargs["api_key"] = rc.api_key
        if rc.url:
            kwargs["base_url"] = rc.url
        kwargs.update(rc.extra_options())
        if not rc.model:
            raise ProviderError("embeddings role needs a model name")
        try:
            self.model = init_embeddings(rc.model, provider=rc.provider, **kwargs)
        except ImportError as exc:
            raise ProviderError(f"{rc.provider} embeddings not installed: {exc}") from exc
        self.model_name = f"{rc.provider}:{rc.model}"
        self.dim = int(rc.dim or 768)

    def embed(self, texts: list[str]) -> tuple[list[np.ndarray], Usage]:
        try:
            vecs = self.model.embed_documents(texts)
        except Exception as exc:
            raise ProviderError(f"{self.model_name}: {exc}") from exc
        out = []
        for v in vecs:
            a = np.asarray(v, dtype=np.float32)
            if a.shape[0] != self.dim:
                raise ProviderError(f"{self.model_name} returned dim {a.shape[0]}, config says {self.dim}")
            n = np.linalg.norm(a)
            out.append(a / n if n else a)
        return out, Usage(tokens_in=sum(estimate_tokens(t) for t in texts))
