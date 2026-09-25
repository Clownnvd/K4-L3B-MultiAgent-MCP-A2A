import subprocess
from pathlib import Path


def distributable_paths(root: Path) -> list[Path]:
    """Inspect tracked and addable source, not ignored runtime payloads."""
    result = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
    )
    return [Path(name) for name in result.decode("utf-8").split("\0") if name]


def test_repository_contains_no_competition_payload() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = distributable_paths(root)
    assert Path("case-set.json") not in paths
    assert not any(p.parts[0] in {"inputs", "outputs", "traces", "runs", ".local"}
                   and p.name != ".gitkeep" for p in paths)
    assert not any(p.name == ".env" or p.suffix in {".pem", ".key", ".zip"} for p in paths)
    forbidden = {"oracles", "reference-outputs", "private-partitions.json", "mcp-access.json"}
    assert not any(set(path.parts) & forbidden for path in paths)


def test_ignored_payload_is_local_but_forced_tracking_is_detected(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("case-set.json\n", encoding="utf-8")
    (tmp_path / "case-set.json").write_text("{}", encoding="utf-8")
    assert Path("case-set.json") not in distributable_paths(tmp_path)
    subprocess.run(["git", "add", "-f", "case-set.json"], cwd=tmp_path, check=True)
    assert Path("case-set.json") in distributable_paths(tmp_path)


def test_example_environment_has_no_real_key() -> None:
    root = Path(__file__).resolve().parents[1]
    content = (root / ".env.example").read_text(encoding="utf-8")
    assert "sk-team-replace_me" in content
    assert content.count("sk-team-") == 1
