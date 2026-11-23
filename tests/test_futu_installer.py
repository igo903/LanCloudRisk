import importlib.util
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SPEC=importlib.util.spec_from_file_location('repair_installer',Path(__file__).resolve().parents[1]/'tools/install_futu_cli.py')
app=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(app)


def archive_bytes(version='v0.6.0',link=False):
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
        binary=tarfile.TarInfo('futu')
        if link:
            binary.type=tarfile.SYMTYPE;binary.linkname='/tmp/elsewhere';archive.addfile(binary)
        else:
            body=b'fixture-official-binary';binary.size=len(body);archive.addfile(binary,io.BytesIO(body))
        data=version.encode();info=tarfile.TarInfo('VERSION');info.size=len(data);archive.addfile(info,io.BytesIO(data))
    return buffer.getvalue()


class InstallerTests(unittest.TestCase):
    def test_mac_intel_and_apple_silicon_use_distinct_official_assets(self):
        self.assertEqual(app.asset_platform('Darwin','arm64'),'darwin_arm64')
        self.assertEqual(app.asset_platform('Darwin','x86_64'),'darwin_amd64')
        with self.assertRaises(app.InstallError):app.asset_platform('Windows','AMD64')

    def test_archive_rejects_links_and_wrong_version(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'fixture.tar.gz'
            for data in (archive_bytes(link=True),archive_bytes('v0.5.0')):
                path.write_bytes(data)
                with self.assertRaises(app.InstallError):app.verified_members(path,'v0.6.0')

    def test_signed_install_is_verified_before_atomic_replacement(self):
        data=archive_bytes();asset='futu_v0.6.0_darwin_arm64.tar.gz'
        def download(_client,url,output=None,**kwargs):
            if url.endswith('.tar.gz'):output.write_bytes(data)
            elif url.endswith('.sig'):output.write_bytes(b'fixture-signature')
            else:output.write_text(hashlib.sha256(data).hexdigest()+'  '+asset+'\n')
        def execute(args,**kwargs):
            if args[1]=='dgst':return subprocess.CompletedProcess(args,0,b'',b'')
            return subprocess.CompletedProcess(args,0,'futu version v0.6.0','')
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'bin/futu';target.parent.mkdir();target.write_bytes(b'old')
            with patch.object(app,'asset_platform',return_value='darwin_arm64'),patch.object(app.shutil,'which',return_value='/test/openssl'), \
                 patch.object(app,'request_url',side_effect=download),patch.object(app.subprocess,'run',side_effect=execute):
                self.assertEqual(app.install(target,'v0.6.0',client=object()),target)
            self.assertEqual(target.read_bytes(),b'fixture-official-binary')
            self.assertEqual(target.stat().st_mode&0o777,0o755)
            self.assertEqual(list(target.parent.glob('.futu-install-*')),[])

    def test_bad_signature_or_checksum_never_replaces_existing_binary(self):
        data=archive_bytes();asset='futu_v0.6.0_darwin_arm64.tar.gz'
        for bad_signature in (True,False):
            def download(_client,url,output=None,**kwargs):
                output.write_bytes(data if url.endswith('.tar.gz') else b'fixture-signature' if url.endswith('.sig') else (('0'*64+'  '+asset+'\n').encode()))
            with self.subTest(bad_signature=bad_signature),tempfile.TemporaryDirectory() as folder:
                target=Path(folder)/'futu';target.write_bytes(b'old-keep-me')
                with patch.object(app,'asset_platform',return_value='darwin_arm64'),patch.object(app.shutil,'which',return_value='/test/openssl'), \
                     patch.object(app,'request_url',side_effect=download),patch.object(app.subprocess,'run',return_value=subprocess.CompletedProcess([],1 if bad_signature else 0,b'',b'')):
                    with self.assertRaises(app.InstallError):app.install(target,'v0.6.0',client=object())
                self.assertEqual(target.read_bytes(),b'old-keep-me')

    def test_auth_probe_does_not_display_cli_response_or_account_metadata(self):
        result=subprocess.CompletedProcess([],1,'{"token":{"status":"not logged in"},"account":{"id":"private-id"}}','private-token')
        with patch.object(app.subprocess,'run',return_value=result),patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertFalse(app.auth_status('/test/futu'))
        self.assertNotIn('private',output.getvalue())
        self.assertIn('需要登录',output.getvalue())


if __name__=='__main__':unittest.main()
