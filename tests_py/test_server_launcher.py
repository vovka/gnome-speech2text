from __future__ import annotations

import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from live.server_launcher import StartupEvidenceDrainer
from live.server_supervisor import main


class StartupEvidenceDrainerTests(unittest.TestCase):
    def test_only_whitelisted_startup_facts_survive_seal(self):
        read_fd, write_fd = os.pipe()
        with TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.txt"
            reader = os.fdopen(read_fd, "rb", buffering=0)
            drainer = StartupEvidenceDrainer(reader, evidence)
            drainer.start()
            os.write(write_fd, b"whisper_init_with_params_no_state: use gpu    = 1\n")
            os.write(write_fd, b"whisper_backend_init_gpu: using CUDA0 backend\n")
            deadline = time.monotonic() + 1
            while "cuda0_backend" not in evidence.read_text() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(drainer.seal(), {"use_gpu", "cuda0_backend"})
            os.write(write_fd, b"later request private text must not persist\n")
            os.close(write_fd)
            drainer.close()
            self.assertEqual(evidence.read_text(), "use_gpu\ncuda0_backend\n")
            self.assertEqual(evidence.stat().st_mode & 0o777, 0o600)

    def test_separator_launch_failure_writes_safe_not_ready_result(self):
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            result = main(["--runtime", str(runtime), "--model", "model", "--port", "18080",
                           "--", "/not/a/server"])
            self.assertEqual(result, 1)
            self.assertEqual((runtime / "server-ready.json").read_text(),
                             '{\n  "ready": false,\n  "reason": "server launch failed"\n}\n')


if __name__ == "__main__":
    unittest.main()
