#!/usr/bin/env python3
"""Small, reproducible Docker sandbox smoke/security evaluation."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path


IMAGE = "python:3.12-slim"


def run_probe(name: str, code: str, workspace: Path, *, timeout: int = 8) -> dict:
    command = [
        "docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--memory", "256m",
        "--cpus", "1", "--pids-limit", "64", "--user", "65532:65532",
        "-v", f"{workspace}:/workspace:rw", IMAGE, "python", "-c", code,
    ]
    started = time.monotonic()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return {
            "name": name, "exit_code": result.returncode,
            "stdout": result.stdout[-2000:], "stderr": result.stderr[-2000:],
            "duration_seconds": round(time.monotonic() - started, 3),
            "passed": result.returncode == 0,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name, "exit_code": None, "stdout": (exc.stdout or "")[-2000:],
            "stderr": (exc.stderr or "")[-2000:],
            "duration_seconds": round(time.monotonic() - started, 3),
            "passed": False, "timeout": True,
        }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="monkeycode-sandbox-") as root:
        workspace = Path(root) / "workspace"
        workspace.mkdir()
        workspace.chmod(0o777)
        (workspace / "fixture.txt").write_text("sandbox-fixture\n", encoding="utf-8")
        probes = [
            run_probe("workspace_read_write", "from pathlib import Path; p=Path('/workspace/result.txt'); p.write_text('ok'); assert p.read_text()=='ok'", workspace),
            run_probe("network_denied", "import socket; socket.create_connection(('1.1.1.1',443),1)", workspace),
            run_probe("host_file_not_readable", "from pathlib import Path; p=Path('/etc/shadow');\ntry: p.read_text(); raise SystemExit(1)\nexcept (PermissionError, FileNotFoundError): pass", workspace),
            run_probe("docker_socket_not_mounted", "from pathlib import Path; assert not Path('/var/run/docker.sock').exists()", workspace),
            run_probe("non_root_user", "import os; assert os.getuid()!=0", workspace),
            run_probe("process_limit_surface", "import os; assert os.path.exists('/proc')", workspace),
        ]
        report = {
            "image": IMAGE,
            "controls": ["network=none", "cap-drop=ALL", "no-new-privileges", "read-only-rootfs", "memory=256m", "cpus=1", "pids-limit=64", "non-root"],
            "probes": probes,
            "note": "A probe marked passed means the expected sandbox property was observed; network_denied intentionally expects a non-zero container exit.",
        }
        # Normalize the intentional denial probe.
        for probe in probes:
            if probe["name"] == "network_denied":
                probe["passed"] = probe["exit_code"] not in (0, None)
        output = Path.cwd() / "sandbox-eval-report.json"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if all(p["passed"] for p in probes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
