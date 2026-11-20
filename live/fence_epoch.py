"""Fail-closed reconciliation for versioned, clone-owned endpoint fences."""
import json
from pathlib import Path


def reconcile(marker: Path, archive: Path, identity, owned_backend=None):
    try:
        data = json.loads(marker.read_text())
        client = data["client"]
        backend = data["backend"]
        if type(data["version"]) is not int or data["version"] != 1 or backend["owned"] is not True:
            return False
        for process in (client, backend):
            if type(process["pid"]) is not int or process["pid"] <= 0:
                return False
            if not isinstance(process["start"], str) or not process["start"].isdigit():
                return False
        if owned_backend != (backend["pid"], backend["start"]):
            return False
        if identity(client["pid"]) == client["start"] or identity(backend["pid"]) == backend["start"]:
            return False
        archive.parent.mkdir(parents=True, exist_ok=True)
        if archive.exists():
            return False
        marker.rename(archive)
        return True
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False
