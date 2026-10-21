import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('bundle_runtime', ROOT / 'tools/bundle-runtime.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)

class PackagingTests(unittest.TestCase):
    def test_geodata_and_licenses_are_required_and_override_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in bundle.GEODATA_NAMES:
                (root / name).write_bytes(b'data')
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(bundle.resource_source('ZFLY_GEODATA', [root], directory=True), root)
            with patch.dict(os.environ, {'ZFLY_GEODATA': str(root / 'missing')}):
                with self.assertRaises(SystemExit):
                    bundle.resource_source('ZFLY_GEODATA', [root], directory=True)
            with self.assertRaises(SystemExit):
                bundle.resource_source('ZFLY_CORE_LICENSE', [root / 'missing'])
            with self.assertRaises(SystemExit):
                bundle.verify_artifacts(root)

    def test_python_patch_versions_are_sorted_numerically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for version in ('3.12.9', '3.12.11'):
                path = root / ('.local/share/uv/python/cpython-' + version + '-macos-aarch64-none/bin')
                path.mkdir(parents=True)
                (path / 'python3.12').touch()
            with patch.object(Path, 'home', return_value=root), patch.object(bundle.platform, 'machine', return_value='arm64'), patch.object(bundle, 'ROOT', root), patch.dict(os.environ, {}, clear=True):
                self.assertIn('3.12.11', str(bundle.python_home()))
