from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeBaseCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


class SearchRequest(StrictModel):
    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["vector", "keyword", "graph", "hybrid"] = "hybrid"
    top_k: int = Field(default=5, ge=1, le=20)
    allow_partial: bool = False
    debug: bool = False

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()

    @model_validator(mode="after")
    def partial_only_hybrid(self):
        if self.allow_partial and self.mode != "hybrid":
            raise ValueError("allow_partial 仅用于 hybrid")
        return self


class AskRequest(StrictModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: Literal["vector", "keyword", "graph", "hybrid"] = "hybrid"
    top_k: int = Field(default=5, ge=1, le=20)
    allow_partial: bool = False

    @model_validator(mode="after")
    def validate_search(self):
        self.search_request()
        return self

    def search_request(self):
        data = self.model_dump()
        data["query"] = data.pop("question")
        return SearchRequest(**data)


class VisionResult(StrictModel):
    description: str = Field(min_length=1, max_length=12000)
    visible_text: list[str] = Field(max_length=100)
    uncertain: list[str] = Field(max_length=50)


class EntityDraft(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    type: Literal["person", "organization", "project", "product", "location", "concept", "other"]


class RelationDraft(StrictModel):
    chunk_id: UUID
    subject: EntityDraft
    predicate: str = Field(min_length=1, max_length=100)
    object: EntityDraft
    quote: str = Field(min_length=1, max_length=3000)
    occurrence: int = Field(ge=0)


class GraphResult(StrictModel):
    relations: list[RelationDraft] = Field(max_length=100)


class Claim(StrictModel):
    text: str = Field(min_length=1, max_length=3000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class GeneratedAnswer(StrictModel):
    status: Literal["answered", "insufficient_evidence", "conflicting_evidence"]
    claims: list[Claim] = Field(max_length=20)
    limitations: list[str] = Field(max_length=20)

    @model_validator(mode="after")
    def answer_needs_claim(self):
        if self.status != "insufficient_evidence" and not self.claims:
            raise ValueError("回答或冲突说明必须包含有引用的结论")
        return self
