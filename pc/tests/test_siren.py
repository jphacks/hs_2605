"""Audio backend/lifecycle tests; no actual sound is played by this suite."""
import io
import os
import threading
import unittest
import wave
from unittest.mock import Mock, patch

from ibuki.server import Siren, enter_acknowledges


class SirenTest(unittest.TestCase):
    def windows_siren(self):
        backend = Mock(SND_FILENAME=0x20000, SND_ASYNC=1, SND_LOOP=8)
        with patch("ibuki.server.sys.platform", "win32"), patch("ibuki.server.importlib.import_module", return_value=backend):
            siren = Siren(True)
        self.addCleanup(siren.stop)
        return siren, backend

    def test_windows_loops_same_wav_as_mac_and_purges_on_acknowledgement(self):
        siren, backend = self.windows_siren()
        siren.start(); path = siren._wav
        self.assertTrue(siren.on); self.assertTrue(siren.enabled)
        backend.PlaySound.assert_called_once_with(path, 0x20000 | 1 | 8)
        with wave.open(path) as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 22050))
            self.assertAlmostEqual(wav.getnframes() / 22050, 1.0, places=3)
        siren.start(); self.assertEqual(backend.PlaySound.call_count, 1)
        siren.stop(); backend.PlaySound.assert_called_with(None, 0)
        self.assertFalse(siren.on); self.assertFalse(os.path.exists(path))
        siren.stop(); self.assertEqual(backend.PlaySound.call_count, 2)
        siren.start(); self.assertTrue(siren.on)
        self.assertNotEqual(siren._wav, path)

    def test_disabled_windows_audio_keeps_existing_logical_alarm_semantics(self):
        with patch("ibuki.server.sys.platform", "win32"), patch("ibuki.server.importlib.import_module") as load:
            siren = Siren(False)
        load.assert_not_called()
        siren.start(); self.assertTrue(siren.on); self.assertFalse(siren.enabled)
        self.assertIsNone(siren._wav)
        siren.stop(); self.assertFalse(siren.on)

    def test_audio_device_failure_does_not_crash_status_processing(self):
        siren, backend = self.windows_siren()
        backend.PlaySound.side_effect = RuntimeError("no audio device")
        siren.start(); path = siren._wav
        self.assertTrue(siren.on)
        siren.stop(); self.assertFalse(siren.on)
        self.assertFalse(os.path.exists(path))

    def test_macos_still_uses_afplay_and_reaps_player_before_shutdown(self):
        playing, stopped = threading.Event(), threading.Event()
        proc = Mock()
        proc.wait.side_effect = lambda timeout=None: stopped.wait(timeout or 2)
        proc.poll.side_effect = lambda: 0 if stopped.is_set() else None
        proc.terminate.side_effect = stopped.set
        def play(*args, **kwargs):
            playing.set()
            return proc
        with patch("ibuki.server.sys.platform", "darwin"), patch("ibuki.server.shutil.which", return_value="/usr/bin/afplay"), patch("ibuki.server.subprocess.Popen", side_effect=play) as popen:
            siren = Siren(True)
            self.addCleanup(siren.stop)
            siren.start(); self.assertTrue(playing.wait(2))
            path = siren._wav
            self.assertEqual(popen.call_args.args[0], ["afplay", path])
            siren.stop()
            proc.terminate.assert_called_once()
            self.assertFalse(siren._thread.is_alive())
            self.assertFalse(os.path.exists(path))

    def test_console_enter_uses_the_same_existing_button_path(self):
        hub = Mock()
        with patch("ibuki.server.sys.stdin", io.StringIO("\n")):
            enter_acknowledges(hub)
        hub.on_line.assert_called_once_with("B,1")


if __name__ == "__main__":
    unittest.main()
