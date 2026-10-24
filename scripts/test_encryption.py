"""Integration checks using a real age binary and temporary test identities."""
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import run_build


class EncryptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.age = Path(os.environ['AGE_TEST_BINARY'])
        self.keygen = self.age.with_name('age-keygen' + self.age.suffix)
        self.key = self.root / 'identity.key'
        subprocess.run([str(self.keygen), '-o', str(self.key)], check=True, capture_output=True)
        recipient = subprocess.check_output([str(self.keygen), '-y', str(self.key)], text=True).strip()
        self.patch = patch.object(run_build, 'RECIPIENT', recipient)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.report = self.root / 'report'
        self.report.mkdir()
        (self.report / 'build.log').write_bytes(b'private diagnostic example')
        self.output = self.root / 'output'

    def test_roundtrip(self):
        run_build.encrypt_result(self.age, self.report, self.output)
        encrypted = self.output / 'build-result.tar.age'
        self.assertEqual([p.name for p in self.output.iterdir()], ['build-result.tar.age'])
        self.assertNotIn(b'private diagnostic example', encrypted.read_bytes())
        decrypted = self.root / 'decrypted.tar'
        subprocess.run([str(self.age), '-d', '-i', str(self.key), '-o', str(decrypted),
                        str(encrypted)], check=True, capture_output=True)
        with tarfile.open(decrypted) as package:
            self.assertEqual(package.extractfile('build.log').read(), b'private diagnostic example')
        self.assertFalse((self.root / 'result.tar').exists())

    def test_tampered_ciphertext_rejected(self):
        run_build.encrypt_result(self.age, self.report, self.output)
        encrypted = self.output / 'build-result.tar.age'
        data = bytearray(encrypted.read_bytes())
        data[-1] ^= 1
        encrypted.write_bytes(data)
        result = subprocess.run([str(self.age), '-d', '-i', str(self.key),
                                 str(encrypted)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_failed_encryption_never_publishes(self):
        with patch.object(run_build, 'RECIPIENT', 'invalid-recipient'):
            with self.assertRaises(subprocess.CalledProcessError):
                run_build.encrypt_result(self.age, self.report, self.output)
        self.assertFalse((self.output / 'build-result.tar.age').exists())


if __name__ == '__main__':
    unittest.main()
