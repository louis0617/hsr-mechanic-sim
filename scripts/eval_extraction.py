#!/usr/bin/env python3
"""F1 evaluation: LLM-extracted graph vs hand-built ground truth."""
from __future__ import annotations

import argparse
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx

from hsrsim.graph.compiler import HSRGraphCompiler
from hsrsim.graph.extractor import GraphExtractor
from hsrsim.catalog.loader import CHARACTERS_DIR, load_character
from hsrsim.llm.client import create_llm_client
from hsrsim.simulator.types import Character

REPO = Path(__file__).resolve().parents[1]
DESCRIPTIONS = REPO / "data/hsr/descriptions"
RESULTS_DIR = REPO / "results"
EXTRACTIONS_DIR = RESULTS_DIR / "extractions"
DEFAULT_CHECKPOINT = RESULTS_DIR / "eval_extraction_checkpoint.json"

from scripts.edge_alignment import (
    aligned_edge_f1,
    build_node_alignment,
    evaluate_edge_scores,
    raw_edge_f1,
)

GROUND_TRUTH_TAGS = ["acheron", "sam", "kafka", "danhengil", "himeko"]


def node_f1(extracted: nx.MultiDiGraph, ground_truth: nx.MultiDiGraph) -> float:
    ext_nodes = {(n, d.get("type")) for n, d in extracted.nodes(data=True)}
    gt_nodes = {(n, d.get("type")) for n, d in ground_truth.nodes(data=True)}
    tp = len(ext_nodes & gt_nodes)
    precision = tp / len(ext_nodes) if ext_nodes else 0.0
    recall = tp / len(gt_nodes) if gt_nodes else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def edge_f1(extracted: nx.MultiDiGraph, ground_truth: nx.MultiDiGraph) -> float:
    """Semantic edge F1 after node alignment (extracted IDs → GT IDs)."""
    stats = build_node_alignment(extracted, ground_truth)
    return aligned_edge_f1(extracted, ground_truth, stats.alignment)


def score_extraction_graphs(
    extracted: nx.MultiDiGraph,
    ground_truth: nx.MultiDiGraph,
) -> dict[str, float | dict]:
    """Node + edge scores for checkpoint / printing."""
    aligned, raw, stats = evaluate_edge_scores(extracted, ground_truth)
    return {
        "node_f1": node_f1(extracted, ground_truth),
        "edge_f1": aligned,
        "raw_edge_f1": raw,
        "alignment_stats": stats.to_dict(),
    }


def tag_to_char_id(tag: str) -> str:
    return {
        "sam": "firefly",
        "danhengil": "dan_heng_il",
    }.get(tag, tag.replace("danhengil", "dan_heng_il"))


def load_checkpoint(path: Path) -> dict:
    if not path.is_file():
        return {"chars": {}, "meta": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_checkpoint(path: Path, checkpoint: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")


def save_extraction(tag: str, char: Character) -> Path:
    EXTRACTIONS_DIR.mkdir(parents=True, exist_ok=True)
    out = EXTRACTIONS_DIR / f"{tag}.json"
    out.write_text(char.model_dump_json(indent=2), encoding="utf-8")
    return out


def load_extraction(path: Path) -> Character:
    return Character.model_validate_json(path.read_text(encoding="utf-8"))


def record_char_result(
    checkpoint: dict,
    *,
    tag: str,
    char_id: str,
    name: str,
    scores: dict[str, float | dict],
    extraction_path: Path,
    checkpoint_path: Path,
) -> None:
    checkpoint.setdefault("chars", {})[tag] = {
        "status": "ok",
        "char_id": char_id,
        "name": name,
        "node_f1": scores["node_f1"],
        "edge_f1": scores["edge_f1"],
        "raw_edge_f1": scores["raw_edge_f1"],
        "alignment_stats": scores["alignment_stats"],
        "extraction_path": str(extraction_path.relative_to(REPO)),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    save_checkpoint(checkpoint_path, checkpoint)


def record_char_failure(
    checkpoint: dict,
    *,
    tag: str,
    char_id: str,
    name: str,
    error: BaseException,
    checkpoint_path: Path,
) -> None:
    checkpoint.setdefault("chars", {})[tag] = {
        "status": "failed",
        "char_id": char_id,
        "name": name,
        "error": str(error)[:500],
        "failed_at": datetime.now(timezone.utc).isoformat(),
    }
    save_checkpoint(checkpoint_path, checkpoint)


def is_success_entry(entry: dict, repo: Path = REPO) -> bool:
    if entry.get("status") == "failed":
        return False
    path = entry.get("extraction_path")
    return bool(path and (repo / path).is_file())


def is_failed_entry(entry: dict) -> bool:
    return entry.get("status") == "failed"


def print_summary(checkpoint: dict, tags: list[str]) -> None:
    node_scores: list[float] = []
    edge_scores: list[float] = []
    raw_edge_scores: list[float] = []
    failed: list[str] = []
    for tag in tags:
        entry = checkpoint.get("chars", {}).get(tag)
        if entry is None:
            continue
        if is_failed_entry(entry):
            failed.append(tag)
            continue
        if "node_f1" in entry:
            node_scores.append(entry["node_f1"])
            edge_scores.append(entry["edge_f1"])
            if "raw_edge_f1" in entry:
                raw_edge_scores.append(entry["raw_edge_f1"])
    if failed:
        print(f"failed tags (skipped): {', '.join(failed)}", flush=True)
    if node_scores:
        print(f"mean node_f1={sum(node_scores) / len(node_scores):.3f} (n={len(node_scores)})", flush=True)
        print(f"mean edge_f1={sum(edge_scores) / len(edge_scores):.3f} (n={len(edge_scores)})", flush=True)
        if raw_edge_scores:
            print(
                f"mean raw_edge_f1={sum(raw_edge_scores) / len(raw_edge_scores):.3f} (n={len(raw_edge_scores)})",
                flush=True,
            )


def _format_scores_line(name: str, scores: dict[str, float | dict], *, suffix: str = "") -> str:
    raw = scores.get("raw_edge_f1")
    if raw is not None:
        return (
            f"{name}: node_f1={scores['node_f1']:.3f} "
            f"edge_f1={scores['edge_f1']:.3f} (raw={raw:.3f}){suffix}"
        )
    return f"{name}: node_f1={scores['node_f1']:.3f} edge_f1={scores['edge_f1']:.3f}{suffix}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chars", type=int, default=5, help="Number of ground-truth chars")
    parser.add_argument("--mock", action="store_true", help="Use ground truth JSON as mock LLM output")
    parser.add_argument(
        "--provider",
        default=None,
        help="LLM provider: deepseek (default) or anthropic",
    )
    parser.add_argument(
        "--no-thinking",
        action="store_true",
        help="Disable DeepSeek thinking mode (cheaper, may hurt quality)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip tags already saved in checkpoint (requires extraction JSON on disk)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run tags even if checkpoint entry exists (including previously failed)",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=DEFAULT_CHECKPOINT,
        help=f"Checkpoint JSON path (default: {DEFAULT_CHECKPOINT.relative_to(REPO)})",
    )
    args = parser.parse_args()

    RESULTS_DIR.mkdir(exist_ok=True)
    compiler = HSRGraphCompiler()
    tags = GROUND_TRUTH_TAGS[: args.chars]
    checkpoint = load_checkpoint(args.checkpoint) if (args.resume or args.checkpoint.is_file()) else {"chars": {}, "meta": {}}
    checkpoint.setdefault("chars", {})
    checkpoint["meta"] = {
        "chars_requested": args.chars,
        "mock": args.mock,
        "provider": args.provider or "deepseek",
        "thinking": not args.no_thinking,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_checkpoint(args.checkpoint, checkpoint)

    if args.mock:
        extractor = GraphExtractor(llm_fn=lambda _p: "")
    else:
        llm = create_llm_client(args.provider, thinking=not args.no_thinking)
        extractor = GraphExtractor(llm_client=llm)
        print(f"LLM provider={args.provider or 'deepseek'} thinking={not args.no_thinking}", flush=True)

    for tag in tags:
        char_id = tag_to_char_id(tag)
        gt_char = load_character(CHARACTERS_DIR / f"{char_id}.json")
        gt_g = compiler.compile(gt_char)

        if args.resume and not args.force and tag in checkpoint["chars"]:
            entry = checkpoint["chars"][tag]
            if is_failed_entry(entry):
                print(
                    f"{entry.get('name', gt_char.name)}: skip (previous failure: {entry.get('error', '')[:120]})",
                    flush=True,
                )
                continue
            ext_path = REPO / entry["extraction_path"]
            if is_success_entry(entry):
                extracted_char = load_extraction(ext_path)
                ext_g = compiler.compile(extracted_char)
                scores = score_extraction_graphs(ext_g, gt_g)
                record_char_result(
                    checkpoint,
                    tag=tag,
                    char_id=char_id,
                    name=entry.get("name", gt_char.name),
                    scores=scores,
                    extraction_path=ext_path,
                    checkpoint_path=args.checkpoint,
                )
                print(_format_scores_line(entry.get("name", gt_char.name), scores, suffix=" (cached)"), flush=True)
                continue
            print(f"{tag}: checkpoint exists but {ext_path} missing — re-extracting", flush=True)

        desc_path = DESCRIPTIONS / f"{tag}.txt"
        if not desc_path.exists():
            print(f"skip {tag}: no description", flush=True)
            continue
        description = desc_path.read_text(encoding="utf-8")

        try:
            if args.mock:
                def llm_fn(_prompt: str, _gt=gt_char) -> str:
                    return _gt.model_dump_json()

                extracted_char = GraphExtractor(llm_fn=llm_fn).extract(description)
            else:
                extracted_char = extractor.extract(description)

            ext_g = compiler.compile(extracted_char)
            scores = score_extraction_graphs(ext_g, gt_g)
            ext_path = save_extraction(tag, extracted_char)
            record_char_result(
                checkpoint,
                tag=tag,
                char_id=char_id,
                name=gt_char.name,
                scores=scores,
                extraction_path=ext_path,
                checkpoint_path=args.checkpoint,
            )
            print(_format_scores_line(gt_char.name, scores, suffix=f" → saved {ext_path.name}"), flush=True)
        except Exception as exc:
            record_char_failure(
                checkpoint,
                tag=tag,
                char_id=char_id,
                name=gt_char.name,
                error=exc,
                checkpoint_path=args.checkpoint,
            )
            print(f"{gt_char.name}: FAILED — {exc}", flush=True)
            print("  → skip, continue next character", flush=True)
            traceback.print_exc()
            continue

    print_summary(checkpoint, tags)


if __name__ == "__main__":
    main()
