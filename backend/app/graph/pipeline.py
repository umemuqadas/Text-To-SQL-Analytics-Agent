"""LangGraph agent workflow.

    retrieve schema -> generate SQL -> validate -> execute -> analyse -> visualise
                        ^                        |          |
                        +------ repair loop <-----+----------+

Generated SQL never reaches the database directly: it passes the validator, and
both validation failures and execution errors feed a bounded repair loop so the
model gets one chance to correct itself before the question comes back rejected.
"""

from __future__ import annotations

import time
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.result_analyzer import analyze
from app.agents.schema_agent import get_catalog, retrieve_schema
from app.agents.sql_generator import generate_sql
from app.agents.visualization_agent import build_visualization
from app.config import Settings, get_settings
from app.services import history_store
from app.services.query_executor import QueryResult, execute_query
from app.services.sql_validator import validate_sql

MAX_ATTEMPTS = 3


class AgentState(TypedDict, total=False):
    question: str
    conversation_id: str
    context_turns: list[dict]
    schema_tables: list[str]
    schema_ddl: str
    matched_values: dict[str, str]
    sql: str
    provider: str
    model: str
    generation_note: str | None
    error_feedback: str | None
    attempts: int
    validation: dict
    execution: dict
    analysis: dict
    visualization: dict
    status: str
    message: str


def _retrieve_schema(state: AgentState) -> dict:
    context = retrieve_schema(state["question"])
    return {
        "schema_tables": context.tables,
        "schema_ddl": context.to_prompt_block(),
        "matched_values": context.matched_values,
    }


def _generate_sql(state: AgentState, settings: Settings) -> dict:
    generated = generate_sql(
        question=state["question"],
        schema_ddl=state.get("schema_ddl", ""),
        context_turns=state.get("context_turns") or None,
        error_feedback=state.get("error_feedback"),
        settings=settings,
        catalog=get_catalog(settings),
        matched_values=state.get("matched_values") or {},
    )
    return {
        "sql": generated.sql,
        "provider": generated.provider,
        "model": generated.model,
        "generation_note": generated.note,
        "attempts": state.get("attempts", 0) + 1,
    }


def _validate_sql(state: AgentState, settings: Settings) -> dict:
    report = validate_sql(state.get("sql", ""), settings=settings)
    payload = report.to_dict()
    if report.ok:
        return {"validation": payload, "sql": report.sql}
    return {"validation": payload, "message": "; ".join(report.errors)}


def _execute(state: AgentState, settings: Settings) -> dict:
    result = execute_query(state["validation"]["sql"], settings)
    return {"execution": result.to_payload(), "message": result.error or ""}


def _repair(state: AgentState) -> dict:
    return {"error_feedback": state.get("message", "The previous query was rejected.")}


def _rejected(state: AgentState) -> dict:
    return {
        "status": "rejected",
        "message": state.get("message") or "The generated SQL was rejected by the safety layer.",
    }


def _failed(state: AgentState) -> dict:
    return {
        "status": "error",
        "message": state.get("message") or "The query failed to execute.",
    }


def _finalize(state: AgentState, settings: Settings) -> dict:
    result = QueryResult.from_payload(state["execution"], state["validation"]["sql"])
    analysis = analyze(state["question"], result, settings)
    visualization = build_visualization(state["question"], result)
    return {
        "status": "executed",
        "analysis": {"answer": analysis.answer, "insights": analysis.insights, "metrics": analysis.metrics, "provider": analysis.provider},
        "visualization": visualization.to_dict(),
    }


def _route_after_validate(state: AgentState) -> Literal["execute", "repair", "reject"]:
    if state["validation"]["ok"]:
        return "execute"
    return "repair" if state.get("attempts", 1) < MAX_ATTEMPTS else "reject"


def _route_after_execute(state: AgentState) -> Literal["finalize", "repair", "fail"]:
    execution = state.get("execution") or {}
    if execution.get("error"):
        return "repair" if state.get("attempts", 1) < MAX_ATTEMPTS else "fail"
    return "finalize"


def build_graph(settings: Settings):
    graph = StateGraph(AgentState)
    graph.add_node("retrieve_schema", _retrieve_schema)
    graph.add_node("generate_sql", lambda state: _generate_sql(state, settings))
    graph.add_node("validate_sql", lambda state: _validate_sql(state, settings))
    graph.add_node("execute", lambda state: _execute(state, settings))
    graph.add_node("repair", _repair)
    graph.add_node("reject", _rejected)
    graph.add_node("fail", _failed)
    graph.add_node("finalize", lambda state: _finalize(state, settings))

    graph.add_edge(START, "retrieve_schema")
    graph.add_edge("retrieve_schema", "generate_sql")
    graph.add_edge("generate_sql", "validate_sql")
    graph.add_conditional_edges("validate_sql", _route_after_validate, {"execute": "execute", "repair": "repair", "reject": "reject"})
    graph.add_conditional_edges("execute", _route_after_execute, {"finalize": "finalize", "repair": "repair", "fail": "fail"})
    graph.add_edge("repair", "generate_sql")
    graph.add_edge("finalize", END)
    graph.add_edge("reject", END)
    graph.add_edge("fail", END)
    return graph.compile()


def ask(question: str, conversation_id: str = "default", settings: Settings | None = None) -> dict:
    """Run the full pipeline for one question and persist the run."""
    settings = settings or get_settings()
    started = time.perf_counter()
    graph = build_graph(settings)
    state: AgentState = {
        "question": question.strip(),
        "conversation_id": conversation_id,
        "context_turns": history_store.recent_turns(conversation_id, settings),
        "attempts": 0,
    }
    final = graph.invoke(state)

    validation = final.get("validation", {})
    execution = final.get("execution", {})
    analysis = final.get("analysis", {})
    status = final.get("status", "error")
    answer = analysis.get("answer") or final.get("message", "")
    visualization = final.get("visualization", {})

    run_id = history_store.record_run(
        question=question,
        sql=validation.get("sql") or final.get("sql", ""),
        status=status,
        summary=answer,
        result_payload=execution,
        settings=settings,
        conversation_id=conversation_id,
        error=final.get("message") if status != "executed" else None,
        provider=final.get("provider", ""),
        chart_type=visualization.get("chart_type", "table"),
        visualization=visualization,
    )

    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "question": question,
        "status": status,
        "answer": answer,
        "sql": validation.get("sql") or final.get("sql", ""),
        "generated_sql": final.get("sql", ""),
        "validation": validation,
        "result": execution,
        "analysis": analysis,
        "visualization": visualization,
        "schema_tables": final.get("schema_tables", []),
        "matched_values": final.get("matched_values", {}),
        "provider": final.get("provider", ""),
        "model": final.get("model", ""),
        "attempts": final.get("attempts", 0),
        "note": final.get("generation_note"),
        "message": final.get("message"),
        "total_ms": int((time.perf_counter() - started) * 1000),
    }
