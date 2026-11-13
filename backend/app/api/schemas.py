"""Request/response models for the analytics API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500, description="Natural-language question.")
    conversation_id: str = Field(default="default", max_length=64)


class ValidateRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=5000, description="SQL to check against the safety rules.")


class ConnectionRequest(BaseModel):
    name: str = Field(default="", max_length=60, description="Label shown in the source list.")
    url: str = Field(min_length=8, max_length=500, description="PostgreSQL or MySQL connection string.")


class ActivateRequest(BaseModel):
    id: str | None = Field(default=None, max_length=40, description="Source to point the agent at; 'demo' restores the sample warehouse.")
