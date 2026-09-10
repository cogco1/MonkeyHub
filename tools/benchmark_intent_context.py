"""Compare prepared intent request text offline; print JSON, write no artifacts.

The capture compiler substitutes only the provider call. Scope selection, state
slicing, rules and output schemas run through the production preparation path.
No model is invoked and no architectural success rate is inferred.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
for path in (REPO, REPO / "apps/archflow-studio/api"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from archflow.project.refs import ProjectVersionRef, RunRef
from archflow.state.operational_state import DesignObligation
from archflow.state.stage_workflow import DesignPhase
from archflow.state.state_record import Entity, Parameter, Relation, StateRecord, ValidatorBinding
from archflow_studio_api.application.binding import ReferenceRun
from archflow_studio_api.application.intent_agent import (
    MAX_OUTPUT_TOKENS, SYSTEM_PROMPT, Selection, _compile_context_request, _prompt,
    record_sheet, response_schema,
)
from archflow_studio_api.application.projection import StateProjection, _elements


def _cached_tokenizer_file(blobpath: str, expected_hash: str | None = None) -> bytes:
    """Read an existing tiktoken cache without downloading or changing it."""

    directory = os.environ.get("TIKTOKEN_CACHE_DIR", os.environ.get(
        "DATA_GYM_CACHE_DIR", str(Path(tempfile.gettempdir()) / "data-gym-cache")))
    if not directory:
        raise ValueError("tiktoken cache is disabled")
    data = (Path(directory) / hashlib.sha1(blobpath.encode()).hexdigest()).read_bytes()
    if expected_hash and hashlib.sha256(data).hexdigest() != expected_hash:
        raise ValueError("cached tokenizer does not match its expected hash")
    return data


def token_counter(choice: str):
    if choice != "heuristic":
        try:
            import tiktoken
            with patch("tiktoken.load.read_file_cached", _cached_tokenizer_file):
                encoding = tiktoken.get_encoding("o200k_base")
            return lambda text: len(encoding.encode(text, disallowed_special=())), "tiktoken:o200k_base"
        except (ImportError, OSError, ValueError) as exc:
            if choice == "o200k_base":
                raise ValueError("o200k_base requires installed tiktoken and its existing local cache; this benchmark does not download it") from exc
    return lambda text: (len(text.encode("utf-8")) + 3) // 4, "heuristic_utf8_bytes_div4"


def fixture(sibling_count: int) -> StateProjection:
    """A selected wall with real graph links and unrelated authored siblings."""

    entities = [
        Entity("building", "Component@1", {"semantic_kind": "building"}),
        Entity("facade", "Component@1", {"semantic_kind": "controlled-entry"}, "building"),
        Entity("level-01", "Level@1", {"role": "ground", "elevation": 0.0}),
        Entity("axis-front", "GridAxis@1", {"role": "front", "origin": [0, 0, 0], "direction": [1, 0, 0]}),
        Entity("wall-type", "Type@1", {"producer": "wall", "params": {"thickness": 0.2}}),
        Entity("wall-07", "Element@1", {
            "component_id": "facade", "producer": "wall", "type_ref": "wall-type",
            "params": {"height": "@wall-height", "thickness": 0.2},
            "references": {"base": {"level": "level-01"}, "line": {
                "from": {"axis_point": {"axis": "front", "along": 0.0}},
                "to": {"axis_point": {"axis": "front", "along": 4.0}},
            }},
        }, "facade"),
        Entity("parapet-01", "Element@1", {
            "component_id": "facade", "producer": "wall", "params": {"height": 0.6, "thickness": 0.2},
            "references": {"base": {"datum": "wall-07-top"}},
        }, "facade"),
        Entity("local-reading", "Reading@1", {"subject_refs": ["entity:wall-07"], "note": "Keep the declared facade alignment"}),
        Entity("global-reading", "Reading@1", {"note": "Retain agreed project limits"}),
    ]
    for index in range(sibling_count):
        entities.extend((
            Entity(f"remote-type-{index}", "Type@1", {"producer": "wall", "params": {"height": 3.0, "thickness": 0.25}}),
            Entity(f"remote-wall-{index}", "Element@1", {
                "component_id": "facade", "producer": "wall", "type_ref": f"remote-type-{index}",
                "params": {"height": 3.0, "thickness": 0.25},
                "references": {"base": {"level": "level-01"}},
            }, "facade"),
        ))
    record = StateRecord(
        "context-benchmark", "offline-fixture", entities=tuple(entities),
        parameters=(Parameter("wall-height", 3.0, "m"),),
        relations=(Relation("parapet-support", "support", "wall-07", "parapet-01",
                            validator=ValidatorBinding("support_contact", tolerance=0.001)),),
        obligations=(DesignObligation("keep-support", "Retain the declared support relationship", "studio:intent",
                                      subject_refs=("entity:wall-07",)),
                     DesignObligation("project-limits", "Preserve agreed project limits", "studio:intent")),
        basis_refs=("studio:intent",),
    )
    elements, error = _elements(record)
    if error:
        raise ValueError(error)
    head = ProjectVersionRef(record.project_id)
    run = RunRef(record.project_id, record.run_id, head)
    return StateProjection(
        project_id=record.project_id, head=head, run=run, reference=ReferenceRun(run, "none", None),
        record=record, record_source="offline-fixture", reference_state_exact=False, reference_state_error=None,
        phase=DesignPhase.DESIGN_DEVELOPMENT, state=None, matches_reference_receipt=None,
        components=None, component_tree_error=None, elements=elements, parameters=record.parameters,
        edges=record.dependency_edges(), honesty=("Synthetic input; geometry and architectural validity are not evaluated.",),
    )


class CaptureCompiler:
    """Replace the external call while exercising the real preparation loop."""

    binding = SimpleNamespace(model_id="offline-benchmark")
    context_budget_tokens = 16_000

    def __init__(self):
        self.prepared = None

    def _compile_once(self, **prepared):
        self.prepared = prepared
        return SimpleNamespace(status="question", receipt=None)


def _measure(message, sheet, schema, rules, count):
    schema_text = json.dumps(schema)
    state_text = json.dumps(sheet, ensure_ascii=False, sort_keys=True)
    # Match the Anthropic adapter's two text fields before SDK framing.
    system = rules + "\n\nJSON schema of the only acceptable answer:\n" + schema_text
    user = _prompt(message, sheet)
    return {
        "characters": len(system + user), "utf8_bytes": len((system + user).encode("utf-8")),
        "input_text_tokens": count(system + user), "schema_tokens": count(schema_text),
        "state_tokens": count(state_text), "system_rules_tokens": count(rules),
    }


def benchmark(sibling_counts, count):
    rows = []
    legacy_schema = response_schema()
    for sibling_count in sibling_counts:
        projection = fixture(sibling_count)
        selection = Selection("facade", "wall-07")
        sheet = record_sheet(projection, selection)
        for expected_tier, message in (
            ("scalar", "set this wall thickness to 0.3"),
            ("component", "set this wall thickness to 0.3 and height to 3.5"),
            ("design", "reorganize the upper gallery while preserving structural and circulation constraints"),
        ):
            compiler = CaptureCompiler()
            # Preflight warnings are already tested by the runtime suite. This
            # command's only output is its machine-readable benchmark result.
            with patch("archflow_studio_api.application.intent_agent.log"):
                _compile_context_request(compiler, message=message, selection=selection,
                                         projection=projection, operation_observer=None)
            prepared = compiler.prepared
            context = prepared["context"]
            if context.tier != expected_tier:
                raise ValueError(f"benchmark case {expected_tier} unexpectedly compiled as {context.tier}")
            legacy = _measure(message, sheet, legacy_schema, SYSTEM_PROMPT, count)
            compiled = _measure(message, context.sheet, prepared["schema"], prepared["rules"], count)
            rows.append({
                "sibling_count": sibling_count, "task_type": context.tier,
                "project_element_count": len(projection.elements),
                "sent_element_count": len(context.sheet["elements"]),
                "sent_type_count": len(context.sheet["types"]),
                "sent_obligation_count": len(context.sheet.get("obligations", [])),
                "max_output_tokens": MAX_OUTPUT_TOKENS[context.tier],
                "legacy": legacy, "compiled": compiled,
                "input_text_tokens_removed": legacy["input_text_tokens"] - compiled["input_text_tokens"],
            })
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--siblings", nargs="+", type=int, default=[0, 100, 1000],
                        help="Unrelated sibling Element/Type pairs per fixture (default: 0 100 1000)")
    parser.add_argument("--tokenizer", choices=("auto", "o200k_base", "heuristic"), default="auto",
                        help="Use locally cached o200k_base if available; never download tokenizer data")
    args = parser.parse_args(argv)
    if any(count < 0 or count > 10_000 for count in args.siblings):
        parser.error("--siblings must be between 0 and 10000")
    try:
        count, estimator = token_counter(args.tokenizer)
    except ValueError as exc:
        parser.error(str(exc))
    result = {
        "counter": estimator, "provider_format": "anthropic_text_before_sdk_framing",
        "count_basis": "local_text_estimate_not_provider_billing", "live_model_calls": 0,
        "notes": [
            "The local encoding is not an exact Anthropic or Codex billing tokenizer.",
            "Section token counts are independent diagnostics and may not sum to concatenated text tokens.",
            "Synthetic requests measure packaging, not model output success, cached usage, geometry or architectural validity.",
        ],
        "cases": benchmark(args.siblings, count),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
