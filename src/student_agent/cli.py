from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .batch import execute_batch
from .cases import load_case_set
from .config import Settings
from .contracts import Contracts
from .mcp_gateway import connect_gateway
from .model_adapter import ModelSettings, OpenAICompatibleModel
from .run_package import package_run, validate_run
from .workflow import Orchestrator


def _root(value: str) -> Path:
    return Path(value).resolve()


async def _show_tools(root: Path) -> None:
    settings = Settings.load(root)
    contracts = Contracts(root / "contracts" / "schemas")
    async with connect_gateway(settings.mcp_endpoint, settings.team_api_key, contracts) as gateway:
        for tool in await gateway.list_tools():
            print(tool)


def _run_directory(root: Path, out: str | None, mode: str) -> Path:
    if out:
        return (root / out).resolve()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return root / "runs" / f"{mode}-{stamp}-{uuid.uuid4().hex[:8]}"


def _print_receipt(run_dir: Path, receipt: dict) -> None:
    print(json.dumps({"run_dir": str(run_dir), "mode": receipt["mode"],
                      "completed": receipt["completed"], "failed": receipt["failed"],
                      "abstained": receipt.get("abstained", 0)}))
    if receipt["failed"]:
        raise RuntimeError("Batch contains failed cases; inspect its receipt and checkpoints")


async def _run(root: Path, *, out: str | None = None, limit: int | None = None,
               concurrency: int = 4, critic: bool = False,
               abstain_on_failure: bool = False) -> None:
    settings = Settings.load(root)
    model = OpenAICompatibleModel(ModelSettings.load())
    critic_model = OpenAICompatibleModel(ModelSettings.load("CRITIC")) if critic else None
    case_set = load_case_set(root)
    contracts = Contracts(root / "contracts" / "schemas")
    if limit is not None and not 1 <= limit <= len(case_set.case_ids):
        raise ValueError("--limit must be between 1 and 100")
    cases = [case_set.cases[case_id] for case_id in case_set.case_ids[:limit]]
    run_dir = _run_directory(root, out, "live")
    solver = Orchestrator(contracts, model, critic=critic_model,
                          allow_abstention=abstain_on_failure)
    async with connect_gateway(settings.mcp_endpoint, settings.team_api_key, contracts) as gateway:
        gateway.available_tools = set(await gateway.list_tools())
        if not gateway.available_tools:
            raise RuntimeError("MCP Gateway returned no tools")
        receipt = await execute_batch(cases, gateway, solver, run_dir, mode="live",
                                      concurrency=concurrency, case_set_version=case_set.version)
    _print_receipt(run_dir, receipt)


async def _demo(root: Path, *, out: str | None, count: int, concurrency: int) -> None:
    from .demo import DemoGateway, DemoModel, demo_cases

    if not 1 <= count <= 100:
        raise ValueError("--count must be between 1 and 100")
    contracts = Contracts(root / "contracts" / "schemas")
    cases = demo_cases(count)
    run_dir = _run_directory(root, out, "demo")
    receipt = await execute_batch(cases, DemoGateway(cases), Orchestrator(contracts, DemoModel()),
                                  run_dir, mode="demo", concurrency=concurrency,
                                  case_set_version="demo-v1")
    _print_receipt(run_dir, receipt)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Day09 L3B student workflow")
    result.add_argument("--root", default=".", help="repository root (default: current directory)")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-inputs", help="validate case-set.json and all 100 inputs")
    commands.add_parser("mcp-tools", help="authenticate and list discovered MCP tools")
    live = commands.add_parser("run", aliases=["live"], help="run authenticated live cases")
    live.add_argument("--limit", type=int, help="investigate a subset; cannot be submitted")
    live.add_argument("--out", help="new isolated run directory (default: runs/live-<unique>)")
    live.add_argument("--concurrency", type=int, default=4)
    live.add_argument("--critic", action="store_true", help="enable configured CRITIC model")
    live.add_argument("--abstain-on-failure", action="store_true",
                      help="allow explicitly traced, verified unknown results when solving fails")
    demo = commands.add_parser("demo", help="run synthetic cases offline; not submittable")
    demo.add_argument("--count", type=int, default=100)
    demo.add_argument("--out", help="new isolated run directory (default: runs/demo-<unique>)")
    demo.add_argument("--concurrency", type=int, default=4)
    validate = commands.add_parser("validate", help="verify a complete live run for submission")
    validate.add_argument("--run-dir", required=True)
    package = commands.add_parser("package", help="validate and build the submission ZIP")
    package.add_argument("--run-dir", required=True)
    package.add_argument("--output", default="dist/submission.zip")
    return result


def main() -> None:
    args = parser().parse_args()
    root = _root(args.root)
    try:
        if args.command == "validate-inputs":
            case_set = load_case_set(root)
            print(
                f"OK: {case_set.variant_id} / {case_set.version} / "
                f"{len(case_set.case_ids)} cases"
            )
        elif args.command == "mcp-tools":
            asyncio.run(_show_tools(root))
        elif args.command in {"run", "live"}:
            asyncio.run(_run(root, out=args.out, limit=args.limit,
                             concurrency=args.concurrency, critic=args.critic,
                             abstain_on_failure=args.abstain_on_failure))
        elif args.command == "demo":
            asyncio.run(_demo(root, out=args.out, count=args.count, concurrency=args.concurrency))
        elif args.command == "validate":
            contracts = Contracts(root / "contracts" / "schemas")
            payloads = validate_run(root / args.run_dir, contracts)
            print(f"OK: {len(payloads) - 2} verified outputs and linked trace")
        elif args.command == "package":
            contracts = Contracts(root / "contracts" / "schemas")
            destination = package_run(root / args.run_dir, root / args.output, contracts)
            print(f"OK: {destination}")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
