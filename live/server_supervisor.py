"""Launch one owned whisper server with a permanent privacy-preserving stderr drainer."""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path

from live.server_launcher import StartupEvidenceDrainer, start_owned_server
from live.adapters import endpoint_marker
from live.fence_epoch import reconcile

REQUIRED_FACTS = {"use_gpu", "gpu_device_0", "device_0", "cuda0_backend", "model_loaded"}


def listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=.2):
            return True
    except OSError:
        return False


def process_start(pid: int) -> str:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return stat.rsplit(")", 1)[1].split()[19]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def reconcile_prior_fence(marker: Path, runtime: Path, endpoint: str, identity) -> bool:
    if not marker.exists():
        return True
    try:
        old = json.loads((runtime / "preview-runner.json").read_text())
        ownership = dict(line.split("=", 1) for line in (runtime / "ownership.txt").read_text().splitlines())
        pid = old["server_pid"]
        attestation = old["attestation"]
        start = attestation["process_started"]
        if (type(pid) is not int or old["endpoint"] != endpoint
                or attestation["process_identity"] != f"pid:{pid}"
                or ownership["owned_pid"] != str(pid) or ownership["start_epoch"] != start):
            return False
        archive = runtime / "fence-archive" / f"{marker.name}.{time.time_ns()}"
        return reconcile(marker, archive, identity, (pid, start))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("server", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    server = args.server[1:] if args.server[:1] == ["--"] else args.server
    if not server:
        return 2
    runtime = args.runtime
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker = endpoint_marker(Path(f"/run/user/{os.getuid()}/speech2text-live"), f"http://127.0.0.1:{args.port}")
    def identity(pid):
        try: return process_start(pid)
        except (FileNotFoundError, ProcessLookupError): return None
    if not reconcile_prior_fence(marker, runtime, f"http://127.0.0.1:{args.port}", identity):
        write_json(runtime / "server-ready.json", {"ready": False, "reason": "unreconciled endpoint fence"})
        return 1
    try:
        owned = start_owned_server(server)
    except OSError:
        write_json(runtime / "server-ready.json", {"ready": False, "reason": "server launch failed"})
        return 1
    assert owned.process.stderr is not None
    drainer = StartupEvidenceDrainer(owned.process.stderr, runtime / "server-startup-evidence.txt")
    drainer.start()
    write_json(runtime / "launch-attempt.json", {"supervisor_pid": os.getpid(), "server_pid": owned.pid,
                                                   "server_pgid": owned.pgid, "logging": "stdout=/dev/null; stderr=startup-sanitizer"})
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline and owned.process.poll() is None:
            if REQUIRED_FACTS <= drainer.facts() and listening(args.port):
                start = process_start(owned.pid)
                facts = drainer.seal()
                config = {"endpoint": f"http://127.0.0.1:{args.port}", "model": args.model,
                          "server_pid": owned.pid,
                          "attestation": {"process_identity": f"pid:{owned.pid}", "process_started": start,
                                          "cuda_verified": True}}
                write_json(runtime / "preview-runner.json", config)
                (runtime / "ownership.txt").write_text(
                    f"supervisor_pid={os.getpid()}\nowned_pid={owned.pid}\nstart_epoch={start}\n"
                    f"server_pgid={owned.pgid}\nlogging=stdout:/dev/null;stderr:sealed-sanitizer-drop-all\n")
                (runtime / "ownership.txt").chmod(0o600)
                write_json(runtime / "server-ready.json", {"server_pid": owned.pid, "start_epoch": start,
                                                             "facts": sorted(facts), "sealed": True})
                return owned.process.wait()
            time.sleep(.05)
        raise RuntimeError("startup CUDA evidence or loopback listener unavailable")
    except Exception:
        owned.process.terminate()
        try:
            owned.process.wait(timeout=3)
        except Exception:
            owned.process.kill(); owned.process.wait()
        drainer.close()
        write_json(runtime / "server-ready.json", {"ready": False, "reason": "startup verification failed"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
