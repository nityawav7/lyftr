"""Request/response models. Field names follow the API contract exactly."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

SessionId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
UserMessage = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: SessionId
    message: UserMessage
    system_prompt: Annotated[str, StringConstraints(max_length=4000)] | None = None

    @field_validator("system_prompt")
    @classmethod
    def blank_prompt_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class AttemptOut(BaseModel):
    key_id: str
    model: str
    status: int | None
    error: str
    simulated: bool


class ChatMetadata(BaseModel):
    resolution_source: Literal["LIVE_INFERENCE", "LLM_CACHE"]
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: str | None
    fallback_attempts: int
    # Extra fields (allowed by the contract): the failover trail for this request.
    attempts: list[AttemptOut] = Field(default_factory=list)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    metadata: ChatMetadata


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["user", "assistant"]
    content: str
    metadata: dict | None = None
    created_at: str | None = None


class HistoryResponse(BaseModel):
    session_id: str
    messages: list[HistoryMessage]
