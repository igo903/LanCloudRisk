import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('risklens', Path(__file__).resolve().parent.parent / 'run_risklens_local.py')
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


class CachePermissions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.primary = self.root / 'old' / 'market-cache.json'
        self.fallback = self.root / 'fallback' / 'market-cache.json'
        self.patch = patch.object(app, 'cache_locations', return_value=[self.primary, self.fallback])
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.snapshot = {'data_kind': 'local', 'instruments': {}, 'as_of': '2026-10-30',
                         'fetched_at': '2026-10-30T15:30:00+08:00'}

    def test_normal_atomic_cache_is_private(self):
        store = app.DataStore(self.primary)
        store.commit_snapshot(self.snapshot)
        self.assertEqual(json.loads(self.primary.read_text()), self.snapshot)
        self.assertEqual(self.primary.stat().st_mode & 0o777, 0o600)
        self.assertTrue(store.state['cache_persisted'])
        self.assertEqual(list(self.primary.parent.glob('*.tmp')), [])

    def test_inaccessible_legacy_temp_is_never_reused(self):
        stale = self.primary.with_suffix('.tmp')
        stale.mkdir(parents=True)
        store = app.DataStore(self.primary)
        store.commit_snapshot(self.snapshot)
        self.assertTrue(stale.is_dir())
        self.assertTrue(self.primary.is_file())
        self.assertIsNone(store.state['cache_warning'])

    def test_unwritable_directory_uses_fallback(self):
        create = app.tempfile.NamedTemporaryFile

        def guarded(*args, **kwargs):
            if Path(kwargs['dir']) == self.primary.parent:
                raise PermissionError(13, 'Permission denied', str(self.primary.parent))
            return create(*args, **kwargs)

        store = app.DataStore(self.primary)
        with patch.object(app.tempfile, 'NamedTemporaryFile', side_effect=guarded):
            store.commit_snapshot(self.snapshot)
        self.assertEqual(store.cache, self.fallback)
        self.assertEqual(json.loads(self.fallback.read_text()), self.snapshot)
        self.assertTrue(store.state['cache_warning'])
        self.assertNotIn('provider', store.state['errors'])

    def test_replace_denied_preserves_old_file_and_cleans_temp(self):
        self.primary.parent.mkdir()
        self.primary.write_text('old data')
        replace = app.os.replace

        def guarded(src, dst):
            if Path(dst) == self.primary:
                raise PermissionError(13, 'Permission denied', str(dst))
            return replace(src, dst)

        store = app.DataStore(self.primary)
        with patch.object(app.os, 'replace', side_effect=guarded):
            store.commit_snapshot(self.snapshot)
        self.assertEqual(self.primary.read_text(), 'old data')
        self.assertEqual(list(self.primary.parent.glob('*.tmp')), [])
        self.assertTrue(self.fallback.is_file())

    def test_all_locations_denied_still_updates_memory(self):
        store = app.DataStore(self.primary)
        with patch.object(app.tempfile, 'NamedTemporaryFile', side_effect=PermissionError(13, 'Permission denied')):
            store.commit_snapshot(self.snapshot)
        self.assertEqual(store.snapshot, self.snapshot)
        self.assertFalse(store.state['cache_persisted'])
        self.assertIsNone(store.state['cache_path'])
        self.assertIn('内存', store.state['cache_warning'])
        self.assertEqual(store.state['errors'], {})

    def test_recovery_clears_failure_warning(self):
        store = app.DataStore(self.primary)
        with patch.object(app.tempfile, 'NamedTemporaryFile', side_effect=PermissionError(13, 'Permission denied')):
            store.commit_snapshot(self.snapshot)
        store.commit_snapshot(self.snapshot)
        self.assertTrue(store.state['cache_persisted'])
        self.assertIsNone(store.state['cache_warning'])

    def test_restart_loads_newer_fallback(self):
        self.primary.parent.mkdir()
        self.fallback.parent.mkdir()
        old = {**self.snapshot, 'fetched_at': '2026-10-29T15:30:00+08:00'}
        self.primary.write_text(json.dumps(old))
        self.fallback.write_text(json.dumps(self.snapshot))
        store = app.DataStore(self.primary)
        self.assertEqual(store.snapshot, self.snapshot)
        self.assertEqual(store.cache, self.fallback)
        self.assertTrue(store.state['cache_warning'])

    def test_invalid_cache_does_not_prevent_startup(self):
        self.primary.parent.mkdir()
        self.primary.write_text('[]')
        store = app.DataStore(self.primary)
        self.assertEqual(store.snapshot['instruments'], {})

    def test_refresh_finishes_research_when_every_cache_write_fails(self):
        assets = [('002281', '光迅科技', None), ('588080', '科创50ETF易方达', None)]
        record = {'as_of': '2026-10-30', 'closed_as_of': '2026-10-29',
                  'latest_quote': {'price': 10, 'timestamp': '2026-10-30T14:00:00+08:00'},
                  'bars': [{'date': '2026-10-29', 'close': 9, 'vol': 100, 'completed': True}]}
        with patch.object(app, 'ASSETS', assets), patch.object(app, 'SECTOR_ASSETS', []), \
             patch.object(app, 'fetch', return_value=b''), \
             patch.object(app, 'parse_quotes', return_value={'sz002281': {'date': '2026-10-30'}, 'sh588080': {'date': '2026-10-30'}}), \
             patch.object(app, 'load_asset', side_effect=lambda a, q: (a[0], copy.deepcopy(record))), \
             patch.object(app, 'verify_previous'), patch.object(app, 'patch_quote'), \
             patch.object(app, 'load_financials', return_value=({'shares': 100, 'profitTTM': 10, 'revenueTTM': 20, 'bps': 1}, [])) as financials, \
             patch.object(app, 'load_holdings', return_value={'rows': []}), \
             patch.object(app, 'load_nav', return_value={'value': 1}), \
             patch.object(app, 'load_announcements', return_value=[]), \
             patch.object(app, 'load_flows', return_value=[]), \
             patch.object(app, 'load_external', return_value=[]), \
             patch.object(app, 'load_geopolitics', return_value={}), \
             patch.object(app.tempfile, 'NamedTemporaryFile', side_effect=PermissionError(13, 'Permission denied')):
            store = app.DataStore(self.primary)
            store.refresh()
        financials.assert_called_once()
        self.assertEqual(len(store.snapshot['instruments']), 2)
        self.assertTrue(store.snapshot['instruments']['002281']['fundamentals'])
        self.assertFalse(store.state['busy'])
        self.assertEqual(store.state['errors'], {})
        self.assertFalse(store.state['cache_persisted'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
