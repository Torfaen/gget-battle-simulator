import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from android_bridge import MUMU_INSTALL_ENV, find_mumu, locate_mumu_tools


class MuMuDiscoveryTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory(prefix='mumu discovery ')
        self.addCleanup(scratch.cleanup)
        self.install = Path(scratch.name).resolve() / 'Custom MuMu'
        tools = self.install / 'nx_main'
        tools.mkdir(parents=True)
        self.adb = str(tools / 'adb.exe')
        self.manager = str(tools / 'MuMuManager.exe')
        Path(self.adb).touch()
        Path(self.manager).touch()
        environment = patch.dict(os.environ, {MUMU_INSTALL_ENV: ''})
        environment.start()
        self.addCleanup(environment.stop)

    def test_explicit_installation_overrides_path(self):
        with patch.dict(os.environ, {MUMU_INSTALL_ENV: str(self.install)}):
            with patch('android_bridge.shutil.which', return_value='other adb'):
                self.assertEqual(locate_mumu_tools(), (self.adb, self.manager))

    def test_registered_installation_when_path_is_empty(self):
        with patch('android_bridge.shutil.which', return_value=None):
            with patch('android_bridge.registered_mumu_directories', return_value=[self.install]):
                self.assertEqual(locate_mumu_tools(), (self.adb, self.manager))

    def test_sdk_adb_uses_manager_from_registered_installation(self):
        sdk_adb = str(self.install.parent / 'SDK' / 'adb.exe')
        with patch('android_bridge.shutil.which', side_effect=[sdk_adb, None]):
            with patch('android_bridge.registered_mumu_directories', return_value=[self.install]):
                self.assertEqual(locate_mumu_tools(), (sdk_adb, self.manager))

    def test_reconnect_uses_instance_port_reported_by_manager(self):
        endpoint = '127.0.0.1:18443'
        status = json.dumps({'0': {'is_android_started': True, 'adb_host_ip': '127.0.0.1', 'adb_port': 18443}})
        responses = [
            subprocess.CompletedProcess([], 0, 'List of devices attached\n'),
            subprocess.CompletedProcess([], 0, status),
            subprocess.CompletedProcess([], 0),
        ]
        with patch('android_bridge.locate_mumu_tools', return_value=(self.adb, self.manager)):
            with patch('android_bridge.subprocess.run', side_effect=responses) as run:
                with patch('android_bridge.adb_shell', return_value='ready'):
                    self.assertEqual(find_mumu(), [self.adb, '-s', endpoint])
        self.assertEqual(run.call_args_list[1].args[0], [self.manager, 'info', '--vmindex', 'all'])
        self.assertEqual(run.call_args_list[2].args[0], [self.adb, 'connect', endpoint])
