import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class LifecycleScriptsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.project = self.root / 'project'
        for directory in ('scripts', 'systemd', 'runtime/live-preview'):
            (self.project / directory).mkdir(parents=True)
        source = Path(__file__).resolve().parents[1]
        for name in ('install-everyday-lifecycle.sh', 'rollback-everyday-lifecycle.sh'):
            shutil.copy(source / 'scripts' / name, self.project / 'scripts' / name)
        shutil.copy(source / 'systemd/speech2text-live-preview.service', self.project / 'systemd')
        bindir = self.root / 'bin'
        bindir.mkdir()
        mock = bindir / 'systemctl'
        mock.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n')
        mock.chmod(0o755)
        self.calls = self.root / 'calls'
        self.env = {**os.environ, 'HOME': str(self.home), 'PATH': f'{bindir}:{os.environ["PATH"]}',
                    'CALLS': str(self.calls)}
        self.target = self.home / '.config/systemd/user/speech2text-live-preview.service'
        self.receipt = self.project / 'runtime/live-preview/everyday-unit-receipt'

    def run_script(self, name):
        return subprocess.run(['sh', str(self.project / 'scripts' / name)], env=self.env,
                              capture_output=True, text=True)

    def calls_text(self):
        return self.calls.read_text() if self.calls.exists() else ''

    def test_unknown_and_mutated_unit_refused_before_systemctl(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text('unknown\n')
        self.assertNotEqual(self.run_script('install-everyday-lifecycle.sh').returncode, 0)
        self.assertNotEqual(self.run_script('rollback-everyday-lifecycle.sh').returncode, 0)
        self.assertEqual(self.calls_text(), '')
        self.target.unlink()
        self.assertEqual(self.run_script('install-everyday-lifecycle.sh').returncode, 0)
        self.calls.unlink()
        self.target.write_text('mutated\n')
        self.assertNotEqual(self.run_script('rollback-everyday-lifecycle.sh').returncode, 0)
        self.assertEqual(self.calls_text(), '')
        self.assertTrue(self.target.exists())

    def test_identical_unowned_unit_is_not_adopted(self):
        self.target.parent.mkdir(parents=True)
        shutil.copy(self.project / 'systemd/speech2text-live-preview.service', self.target)
        self.assertNotEqual(self.run_script('install-everyday-lifecycle.sh').returncode, 0)
        self.assertEqual(self.calls_text(), '')
        self.assertFalse(self.receipt.exists())

    def test_missing_runtime_directory_is_created(self):
        shutil.rmtree(self.project / 'runtime')
        self.assertEqual(self.run_script('install-everyday-lifecycle.sh').returncode, 0)
        self.assertTrue(self.receipt.exists())

    def test_owned_install_idempotence_and_rollback(self):
        self.assertEqual(self.run_script('install-everyday-lifecycle.sh').returncode, 0)
        self.assertTrue(self.receipt.exists())
        self.assertIn('--user enable speech2text-live-preview.service', self.calls_text())
        self.calls.unlink()
        self.assertEqual(self.run_script('install-everyday-lifecycle.sh').returncode, 0)
        self.assertIn('--user enable speech2text-live-preview.service', self.calls_text())
        other = self.target.parent / 'unrelated.service'
        other.write_text('untouched')
        self.assertEqual(self.run_script('rollback-everyday-lifecycle.sh').returncode, 0)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.receipt.exists())
        self.assertEqual(other.read_text(), 'untouched')
