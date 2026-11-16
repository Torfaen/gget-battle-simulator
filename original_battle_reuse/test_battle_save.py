from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from struct import pack
from unittest.mock import patch

from battle_save import CHECKPOINT, CHECKPOINT_HEADER_SIZE, HEADER, MAGIC, read_save_entry, unpack_save, write_save


class BattleSaveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'battle.ggetsave'

    def timeline(self, stage=90510005, invasion=0):
        header = HEADER.pack(MAGIC, stage, invasion, 3, 0)
        records = []
        for index, state in enumerate((b'current', b'before pass', b'opening')):
            fields = CHECKPOINT.pack(12345, 0, len(state), 0, index == 1, 0)
            records.append(fields + bytes(CHECKPOINT_HEADER_SIZE - len(fields) - 4) + pack('<Iii', 1, 1, 9) + state)
        return header + b''.join(records)

    def test_round_trip_preserves_all_native_records(self):
        original = self.timeline()
        write_save(self.path, BytesIO(original))
        output = BytesIO()
        self.assertEqual(unpack_save(self.path, output), (90510005, 0))
        self.assertEqual(output.getvalue(), original)
        self.assertEqual(read_save_entry(self.path), (None, 90510005, 0))

    def test_preview_and_floor_are_available_without_unpacking_history(self):
        original = self.timeline(90230001, 9023000117)
        manifest = self.path.parent / 'manifest.json'
        manifest.write_text('{"invasion_floors": {"17": {"id": 9023000117}}}', encoding='utf-8')
        write_save(self.path, BytesIO(original), preview=b'preview')
        with patch('battle_save.stage_directory', return_value=self.path.parent):
            self.assertEqual(read_save_entry(self.path), (b'preview', 90230001, 17))
            output = BytesIO()
            self.assertEqual(unpack_save(self.path, output), (90230001, 17))
            self.assertEqual(output.getvalue(), original)

    def test_incomplete_history_is_rejected(self):
        write_save(self.path, BytesIO(self.timeline()[:-1]))
        with self.assertRaises(ValueError):
            unpack_save(self.path, BytesIO())
