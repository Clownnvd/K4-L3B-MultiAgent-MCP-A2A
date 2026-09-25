"""Check the exact Git index before committing; never print credential values."""
import re
import subprocess
from pathlib import Path

from dotenv import dotenv_values


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    config = dotenv_values(root / ".env")
    secrets = [value for key, value in config.items() if value and len(value) >= 16
               and any(word in key for word in ("KEY", "TOKEN", "PASSWORD"))]
    issues = []
    for name in filter(None, names):
        path = Path(name)
        runtime = path.parts[0] in {".local", "runs", "inputs", "outputs", "traces"}
        if (name == ".env" or name == "case-set.json" or
                (runtime and path.name != ".gitkeep") or path.suffix in {".pem", ".key", ".zip"}):
            issues.append((name, "forbidden-runtime-path"))
        text = subprocess.check_output(["git", "show", ":" + name], cwd=root).decode(
            "utf-8", errors="replace"
        )
        if any(secret in text for secret in secrets):
            issues.append((name, "local-credential"))
        if re.search(r"sk-team-[A-Za-z0-9_-]{16,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
                     r"|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}", text):
            issues.append((name, "credential-pattern"))
    print(f"Index files scanned: {len(list(filter(None, names)))}")
    print(f"Safety issues: {issues}")
    return bool(issues)


if __name__ == "__main__":
    raise SystemExit(main())
