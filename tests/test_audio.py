import unittest

from meeting_copilot.audio import owned_module_ids, parse_devices, recording_command


class AudioTests(unittest.TestCase):
    def test_cleanup_matches_owned_modules_without_json_indices(self):
        rows = "41\tmodule-null-sink\tsink_name=mine\n42\tmodule-loopback\tsource=mine.monitor sink=headset\n43\tmodule-null-sink\tsink_name=mine_other\n"
        self.assertEqual(owned_module_ids(rows, "mine"), {"41", "42"})

    def test_monitor_is_not_offered_as_microphone(self):
        sinks, mics = parse_devices(
            [{"name": "headset", "description": "Earbuds", "monitor_source": "headset.monitor"}],
            [
                {"name": "headset.monitor", "monitor_of_sink": 3},
                {"name": "headset.mic", "description": "Earbud mic", "monitor_of_sink": 4294967295},
            ],
        )
        self.assertEqual(sinks[0].monitor, "headset.monitor")
        self.assertEqual([d.name for d in mics], ["headset.mic"])

    def test_capture_command_uses_selected_source_without_shell(self):
        command = recording_command("chosen.monitor")
        self.assertIn("--device=chosen.monitor", command)
        self.assertIn("--rate=16000", command)
        self.assertIn("--channels=1", command)
        with self.assertRaises(ValueError):
            recording_command("")


if __name__ == "__main__":
    unittest.main()
