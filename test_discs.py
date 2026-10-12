import json
from pathlib import Path
import struct
import tempfile
import unittest

from discs import image_serial, prepare, DiscLibrary
from unittest.mock import patch


def iso(path, serial):
    data = bytearray(24*2048)
    data[16*2048:16*2048+7] = b'\x01CD001\x01'
    struct.pack_into('<I', data, 16*2048+158, 20)
    struct.pack_into('<I', data, 16*2048+166, 2048)
    name = b'SYSTEM.CNF;1'
    config = ('BOOT = cdrom:\\'+serial+';1\r\n').encode()
    at = 20*2048
    data[at] = 33+len(name)
    struct.pack_into('<I', data, at+2, 21)
    struct.pack_into('<I', data, at+10, len(config))
    data[at+32] = len(name)
    data[at+33:at+33+len(name)] = name
    data[21*2048:21*2048+len(config)] = config
    path.write_bytes(data)
    return path


class DiscTests(unittest.TestCase):
    def test_disc_one_alone_is_a_valid_library_and_later_discs_are_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = iso(root/'disc1.iso', 'SCUS_941.63')
            with patch('pathlib.Path.home', return_value=root):
                library = DiscLibrary(root)
                result = library.add(1, str(path))
                self.assertEqual([d['available'] for d in result], [True, False, False])
                self.assertIsNone(library.needed_message(1))
                self.assertIn('Disc 2', library.needed_message(2))
                self.assertIn('missing', library.needed_message(3))
                self.assertEqual(len(json.loads((root/'discs.json').read_text())['discs']), 1)

    def test_adding_disc_two_clears_only_its_missing_notice(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch('pathlib.Path.home', return_value=root):
                library = DiscLibrary(root)
                path = iso(root/'disc2.iso', 'SCUS_941.64')
                library.add(2, str(path))
                self.assertIsNone(library.needed_message(2))
                self.assertIsNotNone(library.needed_message(3))
                with self.assertRaisesRegex(ValueError, 'Expected USA Disc 3'):
                    library.add(3, str(path))

    def test_removed_image_becomes_missing_after_refresh(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now = [0]
            with patch('pathlib.Path.home', return_value=root):
                library = DiscLibrary(root, clock=lambda: now[0])
                path = iso(root/'disc1.iso', 'SCUS_941.63')
                library.add(1, str(path))
                path.unlink()
                now[0] = 3
                self.assertFalse(library.inventory()[0]['available'])

    def test_serials_and_ordered_playlist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = [iso(root/f'disc{i}.iso', f'SCUS_941.6{i+2}') for i in (1,2,3)]
            result = prepare(paths, root/'out')
            self.assertEqual(image_serial(paths[0]), 'SCUS_941.63')
            self.assertEqual(Path(result['playlist']).read_text().splitlines(), [str(p) for p in paths])
            self.assertFalse(result['runtime_ready'])
            with self.assertRaises(ValueError): prepare(paths[::-1],root/'bad')
            self.assertFalse((root/'bad').exists())

    def test_missing_or_extra_discs_rejected(self):
        for count in (0,1,2,4):
            with self.assertRaises(ValueError): prepare(['missing']*count, 'unused')

    def test_wrong_game_and_truncation_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = iso(Path(temp)/'wrong.iso','SLUS_999.99')
            with self.assertRaises(ValueError): image_serial(path)
            path.write_bytes(path.read_bytes()[:16*2048+7])
            with self.assertRaisesRegex(ValueError,'Truncated'): image_serial(path)

    def test_raw_cue_data_track(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = iso(root/'source.iso','SCUS_941.63').read_bytes()
            raw = bytearray(24*2352)
            for i in range(24): raw[i*2352+24:i*2352+24+2048] = data[i*2048:(i+1)*2048]
            (root/'disc.bin').write_bytes(raw)
            cue = root/'disc.cue'
            cue.write_text('FILE "disc.bin" BINARY\n TRACK 01 MODE2/2352\n INDEX 01 00:00:00\n')
            self.assertEqual(image_serial(cue),'SCUS_941.63')
            cue.write_text(cue.read_text().replace('00:00:00','00:02:00'))
            with self.assertRaises(ValueError): image_serial(cue)


if __name__ == '__main__': unittest.main()
