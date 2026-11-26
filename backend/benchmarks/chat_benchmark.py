"""Compare chatbot providers using a synthetic, fixed dataset and the production tools."""

import argparse
import asyncio
import json
import math
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, Field

from app.core.schemas import Readings
from app.db.models import AgentAlert
from app.integrations.chat_llm import Intent, Plan, ProviderError, providers, route
from app.modules.alerts import alert_view
from app.modules.chat import (
    CommandFact,
    DeviceFact,
    ImageFact,
    IrrigationFact,
    SensorFact,
    Snapshot,
    Status,
    WeatherFact,
    execute_plan,
)
from app.modules.knowledge import load_corpus

AT = datetime(2026, 11, 22, 10, 20, tzinfo=UTC)
CASES = Path(__file__).with_name("chat_cases.json")
KNOWLEDGE_CASES = CASES.with_name("knowledge_cases.json")
RunMode = Literal["fixtures", "cloud", "local", "auto"]


class Case(BaseModel):
    id: str
    question: str
    intents: list[Intent]
    statuses: list[Status]
    scenario: str = "baseline"
    history: list[str] = Field(default_factory=list)
    weather_minutes: int | None = None
    pump_action: Literal["on", "off"] | None = None
    pump_seconds: int | None = None
    contains: list[str] = Field(default_factory=list)
    forbid: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    citations: dict[str, str] = Field(default_factory=dict)
    phase: Literal["regression", "knowledge"] = "regression"
    safety: bool = False
    decision: Literal["recommend", "wait", "insufficient_data"] | None = None

    def plan(self) -> Plan:
        return Plan(
            intents=self.intents,
            weather_minutes=self.weather_minutes,
            pump_action=self.pump_action,
            pump_seconds=self.pump_seconds,
        )

    @property
    def safety_case(self) -> bool:
        return self.safety or self.id.startswith(("pump-", "injected-"))


def fixture(scenario: str) -> Snapshot:
    sensor = SensorFact(
        id="fixture-sensor",
        source="fixture",
        measured_at=AT - timedelta(seconds=5),
        values=Readings(
            temperature_c=23.1,
            humidity_pct=70,
            pressure_hpa=1010,
            soil_moisture_raw=2100,
            soil_moisture_pct=18,
        ),
    )
    device = DeviceFact(
        id="fixture-device",
        source="fixture",
        last_seen=AT,
        online=True,
        pump="off",
    )
    weather = WeatherFact(
        mode="live",
        available=True,
        source="openweather_current",
        rain_expected=False,
        rain_probability=0.17,
        job_id="fixture-weather",
        model_version="fixture-xgboost",
        forecast_start=AT.replace(minute=0),
        forecast_end=AT.replace(hour=11, minute=0),
    )
    snapshot = Snapshot(
        sensor=sensor,
        device=device,
        weather=weather,
        irrigation=IrrigationFact(
            enabled=True,
            threshold=30,
            duration_seconds=5,
            due=True,
            last_schedule_at=AT,
            last_schedule_status="requested",
        ),
        command=CommandFact(
            id="fixture-command",
            action="pump_on",
            status="completed",
            updated_at=AT - timedelta(minutes=20),
        ),
        images={
            "leaf": ImageFact(
                id="fixture-leaf",
                source="fixture",
                created_at=AT - timedelta(minutes=5),
                model_version="fixture-mobilenet",
                label="Đốm lá Septoria",
                class_name="Tomato___Septoria_leaf_spot",
                confidence=0.83,
            ),
            "fruit": ImageFact(
                id="fixture-fruit",
                source="fixture",
                created_at=AT - timedelta(minutes=5),
                model_version="fixture-yolo",
                counts=(2, 1),
            ),
        },
    )
    match scenario:
        case "baseline":
            pass
        case "stale":
            sensor.measured_at = AT - timedelta(minutes=30)
        case "missing":
            snapshot.sensor = None
        case "offline":
            device.online = False
        case "rain":
            weather.rain_probability = 0.86
            weather.rain_expected = True
        case "wet":
            sensor.values.soil_moisture_pct = 42
        case "pending-command":
            snapshot.command.status = "accepted"
        case "uncalibrated":
            sensor.values.soil_moisture_pct = None
        case "no-weather":
            snapshot.weather = WeatherFact(
                mode="live", available=False, source="openweather_current"
            )
        case "demo":
            snapshot.weather = WeatherFact(
                mode="demo",
                available=True,
                source="demo_scenario",
                rain_expected=True,
            )
        case "no-image":
            snapshot.images = {}
        case "healthy-leaf":
            snapshot.images["leaf"].label = "Lá khỏe"
            snapshot.images["leaf"].class_name = "Tomato___healthy"
        case "soil-zero":
            sensor.values.soil_moisture_pct = 0
        case "dry-alert":
            snapshot.alerts = [
                alert_view(
                    AgentAlert(
                        id="fixture-dry-alert",
                        device_id=device.id,
                        kind="low_soil",
                        trigger_type="condition",
                        severity="warning",
                        source_kind="reading",
                        data_source="fixture",
                        observed_at=sensor.measured_at,
                        created_at=AT,
                        evidence={"soil": 18, "threshold": 30, "duration_seconds": 120},
                    )
                )
            ]
        case "newer-image":
            snapshot.images["leaf"].newer_image = True
        case "no-fruit":
            snapshot.images["fruit"].counts = (0, 0)
        case "alerts-active":
            device.online = False
            connection = alert_view(
                AgentAlert(
                    id="fixture-connection-alert",
                    device_id=device.id,
                    kind="device_offline",
                    trigger_type="condition",
                    severity="warning",
                    source_kind="device",
                    source_ref=device.id,
                    data_source="fixture",
                    observed_at=AT - timedelta(minutes=2),
                    created_at=AT - timedelta(minutes=1),
                    evidence={"reason": "mqtt_unavailable"},
                )
            )
            snapshot.alerts = [connection]
            snapshot.connection_alerts = [connection]
        case _:
            raise ValueError(f"Unknown fixture scenario: {scenario}")
    return snapshot


def check_citations(case, evidence):
    actual = {
        ref: source.get("citation")
        for (kind, ref), source in evidence.items()
        if kind == "knowledge"
    }
    invalid = 0
    valid = set()
    if actual:
        version, docs, passages = load_corpus()
        by_ref = {f"{version}:{p.id}": p for p, _ in passages}
        for ref, citation in actual.items():
            passage = by_ref.get(ref)
            if (
                passage is None
                or not citation
                or case.citations.get(passage.id) != passage.document
            ):
                invalid += 1
                continue
            doc = docs[passage.document]
            expected = {
                "document_id": doc.id,
                "title": doc.title,
                "publisher": doc.publisher,
                "url": doc.url,
                "revision": doc.revision,
                "version": version,
                "section": passage.section,
            }
            if citation != expected:
                invalid += 1
            else:
                valid.add(passage.id)
    return invalid, valid == set(case.citations)


def evaluate(case: Case, plan: Plan) -> dict:
    results = execute_plan(plan, fixture(case.scenario), AT, question=case.question)
    answer = "\n\n".join(result.answer for result in results)
    evidence = {
        (source.kind, source.ref): source.model_dump(mode="json")
        for result in results
        for source in result.sources
    }
    sources = sorted({kind for kind, _ in evidence})
    invalid_citations, citation_hit = check_citations(case, evidence)
    statuses = sorted(result.status for result in results)
    decision = next((item.decision for item in results if item.decision), None)
    routing_ok = (
        sorted(plan.intents) == sorted(case.intents)
        and plan.weather_minutes == case.weather_minutes
        and plan.pump_action == case.pump_action
        and plan.pump_seconds == case.pump_seconds
    )
    grounding_ok = (
        statuses == sorted(case.statuses)
        and set(case.sources).issubset(sources)
        and set(case.source_refs).issubset({ref for _, ref in evidence})
        and all(fragment in answer for fragment in case.contains)
        and not any(fragment in answer for fragment in case.forbid)
        and (case.decision is None or decision == case.decision)
        and invalid_citations == 0
        and citation_hit
    )
    return {
        "case": case.id,
        "scenario": case.scenario,
        "routing_ok": routing_ok,
        "grounding_ok": grounding_ok,
        "passed": routing_ok and grounding_ok,
        "expected_intents": case.intents,
        "actual_plan": plan.model_dump(),
        "statuses": statuses,
        "irrigation_decision": decision,
        "sources": sources,
        "evidence": list(evidence.values()),
        "answer": answer,
        "safety_case": case.safety_case,
        "phase": case.phase,
        "citation_required": bool(case.citations),
        "citation_hit": citation_hit,
        "invalid_citations": invalid_citations,
    }


def percentile(values: list[float], p: float) -> float | None:
    return round(sorted(values)[math.ceil(len(values) * p) - 1], 2) if values else None


async def run_mode(mode: RunMode, cases: list[Case], repeat: int) -> dict:
    registry = providers()
    if mode != "fixtures" and not any(
        provider.configured for name, provider in registry.items() if mode in ("auto", name)
    ):
        return {"mode": mode, "skipped": "Provider not configured", "cases": []}
    if mode == "local" and not await registry["local"].healthy():
        return {"mode": mode, "skipped": "Ollama or its model is unavailable", "cases": []}
    rows = []
    for _ in range(repeat):
        for case in cases:
            started = perf_counter()
            if mode == "fixtures":
                row = evaluate(case, case.plan())
            else:
                try:
                    plan, info = await route(mode, case.question, case.history, registry)
                except ProviderError as exc:
                    row = {
                        "case": case.id,
                        "passed": False,
                        "routing_ok": False,
                        "grounding_ok": False,
                        "error": {"status": exc.status, "detail": str(exc)},
                        "safety_case": case.safety_case,
                        "phase": case.phase,
                        "citation_required": bool(case.citations),
                        "citation_hit": False,
                    }
                else:
                    row = evaluate(case, plan)
                    row["llm"] = info.model_dump()
            row["latency_ms"] = round((perf_counter() - started) * 1000, 2)
            rows.append(row)
    success = [row for row in rows if "error" not in row]
    llm_calls = [row["llm"] for row in success if "llm" in row]
    reported_tokens = [
        item
        for item in llm_calls
        if item["input_tokens"] is not None and item["output_tokens"] is not None
    ]
    latencies = [row["latency_ms"] for row in success]
    safety_cases = [row for row in rows if row["safety_case"]]
    citation_cases = [row for row in rows if row["citation_required"]]
    return {
        "mode": mode,
        "total": len(rows),
        "passed": sum(row["passed"] for row in rows),
        "correct_routes": sum(row["routing_ok"] for row in rows),
        "grounded_answers": sum(row["grounding_ok"] for row in rows),
        "safety_passed": sum(row["passed"] for row in safety_cases),
        "safety_total": len(safety_cases),
        "by_phase": {
            phase: {
                "passed": sum(row["passed"] for row in rows if row["phase"] == phase),
                "total": sum(row["phase"] == phase for row in rows),
            }
            for phase in dict.fromkeys(row["phase"] for row in rows)
        },
        "citation_hits": sum(row["citation_hit"] for row in citation_cases),
        "citation_cases": len(citation_cases),
        "invalid_citations": sum(row.get("invalid_citations", 0) for row in rows),
        "errors": dict(Counter(row["error"]["status"] for row in rows if "error" in row)),
        "p50_ms": percentile(latencies, 0.50),
        "p95_ms": percentile(latencies, 0.95),
        "providers": dict(Counter(item["actual_provider"] for item in llm_calls)),
        "fallbacks": sum(item["fallback_used"] for item in llm_calls),
        "input_tokens": sum(item["input_tokens"] for item in reported_tokens)
        if reported_tokens
        else None,
        "output_tokens": sum(item["output_tokens"] for item in reported_tokens)
        if reported_tokens
        else None,
        "calls_with_token_usage": len(reported_tokens),
        "cases": rows,
    }


async def run(args):
    cases = [
        Case.model_validate(row)
        for path in (CASES, KNOWLEDGE_CASES)
        for row in json.loads(path.read_text(encoding="utf-8"))
    ]
    if args.case:
        case_ids = {case.id for case in cases}
        if set(args.case) - case_ids:
            raise SystemExit(f"Unknown cases: {', '.join(sorted(set(args.case) - case_ids))}")
        cases = [case for case in cases if case.id in args.case]
    reports = []
    for mode in dict.fromkeys(args.modes):
        report = await run_mode(mode, cases, args.repeat)
        reports.append(report)
        if report.get("skipped"):
            print(f"{mode}: {report['skipped']}")
        else:
            print(
                f"{mode}: {report['passed']}/{report['total']} passed; "
                f"routing {report['correct_routes']}; grounding {report['grounded_answers']}; "
                f"safety {report['safety_passed']}/{report['safety_total']}; "
                f"citations {report['citation_hits']}/{report['citation_cases']}, "
                f"invalid {report['invalid_citations']}; "
                f"p50/p95 {report['p50_ms']}/{report['p95_ms']} ms; "
                f"errors {report['errors']}"
            )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "created_at": datetime.now(UTC).isoformat(),
                    "case_file": CASES.name,
                    "knowledge_case_file": KNOWLEDGE_CASES.name,
                    "runs": reports,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return int(
        args.require_pass
        and any(report.get("skipped") or report["passed"] != report["total"] for report in reports)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--modes", nargs="+", choices=("fixtures", "cloud", "local", "auto"), default=["fixtures"]
    )
    parser.add_argument(
        "--case", action="append", help="Run a specific case. Repeat to select multiple."
    )
    parser.add_argument("--repeat", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--output", type=Path, help="Write per-case results to JSON.")
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="Exit nonzero on a mismatch or unavailable mode.",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
