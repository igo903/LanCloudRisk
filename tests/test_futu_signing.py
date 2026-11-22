import base64
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location('risklens_futu', Path(__file__).resolve().parent.parent / 'run_risklens_local.py')
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


class FutuSigningEnvironment(unittest.TestCase):
    def test_mac_uses_homebrew_when_path_openssl_is_libressl(self):
        def version(command, **kwargs):
            output = b'LibreSSL 2.8.3' if command[0] == '/usr/bin/openssl' else b'OpenSSL 3.5.7'
            return subprocess.CompletedProcess(command, 0, output)
        with patch.dict(os.environ, {}, clear=True), patch.object(app.sys, 'platform', 'darwin'), \
             patch.object(app.shutil, 'which', return_value='/usr/bin/openssl'), \
             patch.object(app.subprocess, 'run', side_effect=version):
            self.assertEqual(app.futu_openssl(), '/opt/homebrew/opt/openssl@3/bin/openssl')

    def test_intel_mac_falls_back_when_apple_silicon_binary_is_missing(self):
        def version(command, **kwargs):
            if command[0].startswith('/opt/homebrew'):
                raise FileNotFoundError()
            output = b'LibreSSL 2.8.3' if command[0] == '/usr/bin/openssl' else b'OpenSSL 3.5.7'
            return subprocess.CompletedProcess(command, 0, output)
        with patch.dict(os.environ, {}, clear=True), patch.object(app.sys, 'platform', 'darwin'), \
             patch.object(app.shutil, 'which', return_value='/usr/bin/openssl'), \
             patch.object(app.subprocess, 'run', side_effect=version):
            self.assertEqual(app.futu_openssl(), '/usr/local/opt/openssl@3/bin/openssl')

    def test_missing_supported_openssl_has_install_hint(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(app.shutil, 'which', return_value=None), \
             patch.object(app.subprocess, 'run', side_effect=FileNotFoundError()):
            with self.assertRaisesRegex(app.QuoteUnavailable, 'brew install openssl@3'):
                app.futu_openssl()

    def test_explicit_invalid_override_is_not_silently_ignored(self):
        with patch.dict(os.environ, {'FUTU_OPENSSL_PATH': '/custom/openssl'}), \
             patch.object(app.subprocess, 'run', side_effect=PermissionError()) as run:
            with self.assertRaisesRegex(app.QuoteUnavailable, 'FUTU_OPENSSL_PATH'):
                app.futu_openssl()
            self.assertEqual(run.call_count, 1)

    def test_key_parse_failure_never_exposes_openssl_stderr(self):
        error = subprocess.CalledProcessError(1, ['openssl'], stderr=b'fixture-private-secret')
        with patch.object(app.subprocess, 'run', side_effect=error):
            with self.assertRaisesRegex(app.QuoteUnavailable, 'PEM 私钥无法解析') as result:
                app.futu_private_key_algorithm('openssl', 'key.pem')
        self.assertNotIn('fixture-private-secret', str(result.exception))

    def test_oauth_does_not_require_signing_environment(self):
        with patch.object(app, 'futu_openssl', side_effect=AssertionError('unexpected signer')):
            self.assertEqual(app.futu_headers('https://example.test', {'mode': 'oauth', 'api_key': 'fixture'}, None),
                             {'Authorization': 'Bearer fixture'})


class FutuRealSignatures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.openssl = shutil.which('openssl')
        if not cls.openssl:
            raise unittest.SkipTest('OpenSSL is required for signature verification')
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        cls.keys = {}
        for label, arguments in [('Ed25519', ['-algorithm', 'ED25519']),
                                 ('RSA-SHA256', ['-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048']),
                                 ('EC', ['-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-256'])]:
            path = Path(cls.folder.name) / (label + '.pem')
            subprocess.run([cls.openssl, 'genpkey', *arguments, '-out', str(path)],
                           capture_output=True, check=True, timeout=10)
            cls.keys[label] = path.read_text()

    def credentials(self, label):
        return {'mode': 'appkey', 'api_key': 'fixture-app-id', 'algorithm': label,
                'private_key': self.keys[label]}

    def test_get_and_post_signatures_verify_for_both_algorithms(self):
        url = 'https://webapi.futunn.com/api/v1.0/quote/history-kline?code=US.AAPL&cursor=a%2Bb'
        for algorithm in ('Ed25519', 'RSA-SHA256'):
            for body in (None, b'{"code":"US.AAPL"}'):
                with self.subTest(algorithm=algorithm, body=body), \
                     patch.object(app, 'futu_openssl', return_value=self.openssl):
                    headers = app.futu_headers(url, self.credentials(algorithm), body)
                    payload = '\n'.join((headers['X-Timestamp'], 'GET' if body is None else 'POST',
                                          '/api/v1.0/quote/history-kline', 'code=US.AAPL&cursor=a%2Bb',
                                          '' if body is None else hashlib.sha256(body).hexdigest())).encode()
                    root = Path(self.folder.name)
                    key, public, message, signature = [root / name for name in ('key.pem', 'public.pem', 'message', 'signature')]
                    key.write_text(self.keys[algorithm])
                    subprocess.run([self.openssl, 'pkey', '-in', str(key), '-pubout', '-out', str(public)],
                                   capture_output=True, check=True, timeout=5)
                    message.write_bytes(payload)
                    signature.write_bytes(base64.b64decode(headers['Authorization'], validate=True))
                    if algorithm == 'Ed25519':
                        command = [self.openssl, 'pkeyutl', '-verify', '-rawin', '-pubin', '-inkey', str(public),
                                   '-sigfile', str(signature), '-in', str(message)]
                    else:
                        command = [self.openssl, 'dgst', '-sha256', '-verify', str(public),
                                   '-signature', str(signature), str(message)]
                    subprocess.run(command, capture_output=True, check=True, timeout=5)
                    self.assertEqual(headers['X-Api-Key'], 'fixture-app-id')
                    self.assertNotIn('PRIVATE KEY', str(headers))

    def test_both_algorithm_mismatches_are_actionable(self):
        for actual, selected in [('Ed25519', 'RSA-SHA256'), ('RSA-SHA256', 'Ed25519')]:
            with self.subTest(actual=actual), patch.object(app, 'futu_openssl', return_value=self.openssl):
                credentials = {**self.credentials(actual), 'algorithm': selected}
                with self.assertRaisesRegex(app.QuoteUnavailable, '私钥是 ' + actual):
                    app.futu_headers('https://example.test', credentials, None)

    def test_invalid_pem_and_unsupported_key_type(self):
        for pem, hint in [('-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n', 'PEM 私钥无法解析'),
                          (self.keys['EC'], '私钥类型不支持')]:
            with self.subTest(hint=hint), patch.object(app, 'futu_openssl', return_value=self.openssl):
                credentials = {**self.credentials('Ed25519'), 'private_key': pem}
                with self.assertRaisesRegex(app.QuoteUnavailable, hint) as result:
                    app.futu_headers('https://example.test', credentials, None)
                self.assertNotIn(pem, str(result.exception))

    def test_signer_timeout_is_reported_separately(self):
        with patch.object(app, 'futu_openssl', return_value=self.openssl), \
             patch.object(app, 'futu_private_key_algorithm', side_effect=subprocess.TimeoutExpired('openssl', 5)):
            with self.assertRaisesRegex(app.QuoteUnavailable, '签名超时'):
                app.futu_headers('https://example.test', self.credentials('Ed25519'), None)


if __name__ == '__main__':
    unittest.main()
