import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from live.fence_epoch import reconcile
from live.adapters import EndpointFence
from live.server_supervisor import reconcile_prior_fence


class FenceTests(unittest.TestCase):
    def test_reconciliation_requires_owned_dead_epochs(self):
        with TemporaryDirectory() as directory:
            marker, archive = Path(directory) / 'fence', Path(directory) / 'archive'
            data = {'version': 1, 'client': {'pid': 1, 'start': '10'},
                    'backend': {'pid': 2, 'start': '20', 'owned': True}}
            for content, identity, owned in [
                ('inflight\n', lambda _: None, (2, '20')),
                (json.dumps(data), lambda pid: '10' if pid == 1 else None, (2, '20')),
                (json.dumps(data), lambda pid: '20' if pid == 2 else None, (2, '20')),
                (json.dumps(data), lambda _: None, (3, '20')),
                (json.dumps(data), lambda _: (_ for _ in ()).throw(PermissionError()), (2, '20')),
            ]:
                marker.write_text(content)
                self.assertFalse(reconcile(marker, archive, identity, owned))
                self.assertTrue(marker.exists())
            marker.write_text(json.dumps(data))
            self.assertTrue(reconcile(marker, archive, lambda _: '99', (2, '20')))
            self.assertTrue(archive.exists())

    def test_adapter_to_supervisor_reconciliation(self):
        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            marker = runtime / 'marker'
            metadata = {'version': 1, 'client': {'pid': 1, 'start': '10'},
                        'backend': {'pid': 2, 'start': '20', 'owned': True}}
            (runtime / 'preview-runner.json').write_text(json.dumps({
                'endpoint': 'http://127.0.0.1:18080', 'server_pid': 2,
                'attestation': {'process_identity': 'pid:2', 'process_started': '20'}}))
            ownership = runtime / 'ownership.txt'
            ownership.write_text('owned_pid=2\nstart_epoch=20\n')
            endpoint = 'http://127.0.0.1:18080'
            for identity in (lambda pid: '10' if pid == 1 else None,
                             lambda pid: '20' if pid == 2 else None,
                             lambda _: (_ for _ in ()).throw(PermissionError())):
                EndpointFence(marker, metadata).begin()
                self.assertFalse(reconcile_prior_fence(marker, runtime, endpoint, identity))
                marker.unlink()
            EndpointFence(marker, metadata).begin()
            ownership.write_text('owned_pid=3\nstart_epoch=20\n')
            self.assertFalse(reconcile_prior_fence(marker, runtime, endpoint, lambda _: None))
            ownership.write_text('owned_pid=2\nstart_epoch=20\n')
            self.assertTrue(reconcile_prior_fence(marker, runtime, endpoint, lambda _: None))
            self.assertFalse(marker.exists())
            marker.write_text('inflight\n')
            self.assertFalse(reconcile_prior_fence(marker, runtime, endpoint, lambda _: None))

    def test_malformed_metadata_fails_closed(self):
        with TemporaryDirectory() as directory:
            marker, archive = Path(directory) / 'fence', Path(directory) / 'archive'
            for pid in (True, 0, -1, '2'):
                marker.write_text(json.dumps({'version': 1, 'client': {'pid': pid, 'start': '10'},
                                              'backend': {'pid': 2, 'start': '20', 'owned': True}}))
                self.assertFalse(reconcile(marker, archive, lambda _: None, (2, '20')))
