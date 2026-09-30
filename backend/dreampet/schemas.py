"""Structured outputs the LLM roles produce. Live adapters use `with_structured_output`; the
fake provider builds the same objects deterministically."""

from __future__ import annotations

from pydantic import BaseModel, Field


class QueryPlan(BaseModel):
    questions: list[str] = Field(description="1-3 concrete questions to answer")
    queries: list[str] = Field(description="1-3 web search queries")


class Prediction(BaseModel):
    gist: str = Field(description="What you expect the article says, in 1-2 sentences")


class ReadNotes(BaseModel):
    summary: str = Field(description="2-4 sentence summary in your own words")
    facts: list[str] = Field(default_factory=list, description="up to 4 short facts")
    importance: float = Field(0.5, ge=0, le=1, description="how important this is to remember")
    salience: float = Field(0.3, ge=0, le=1, description="emotional salience: how striking, eerie, delightful")
    safe: bool = Field(True, description="false if the content is harmful or explicit")


class Insight(BaseModel):
    text: str
    memory_ids: list[str]


class Insights(BaseModel):
    insights: list[Insight] = Field(default_factory=list)


class DreamElement(BaseModel):
    text: str = Field(description="one scene of the dream")
    memory_ids: list[str] = Field(description="ids of the memories this scene draws on (at least one)")
    image_hint: str = Field(description="a short visual description for an illustrator")


class DreamDraft(BaseModel):
    title: str
    mood: str
    narrative: str = Field(description="150-400 words, first person")
    elements: list[DreamElement]


class Critique(BaseModel):
    score: float = Field(ge=0, le=1, description="how vivid, coherent-yet-strange and grounded")
    notes: str = ""


class StyleBible(BaseModel):
    look: str
    palette: list[str]
    camera: str


class Shot(BaseModel):
    id: int
    seconds: float
    prompt: str
    negative: str = ""
    element_ref: int = 0
    transition: str = "dissolve"


class Screenplay(BaseModel):
    title: str
    style_bible: StyleBible
    total_seconds: float
    shots: list[Shot]
    narration: str | None = None


class DriftChange(BaseModel):
    field: str = Field(description="'interests.<topic>' or 'traits.<trait>'")
    delta: float = Field(description="points to add, between -3 and 3")
    reason: str
    memory_ids: list[str]


class DriftProposal(BaseModel):
    changes: list[DriftChange] = Field(default_factory=list)
