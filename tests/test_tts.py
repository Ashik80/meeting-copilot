import unittest

from meeting_copilot.tts import should_speak


class TTSTests(unittest.TestCase):
    def test_only_selected_insights_are_spoken(self):
        question = {"type": "QUESTION", "priority": "MEDIUM"}
        concern = {"type": "CONCERN", "priority": "HIGH"}
        self.assertFalse(should_speak(question, "disabled"))
        self.assertFalse(should_speak(question, "high"))
        self.assertTrue(should_speak(concern, "high"))
        self.assertTrue(should_speak(question, "questions"))
        self.assertFalse(should_speak(concern, "questions"))
        self.assertTrue(should_speak(concern, "questions_concerns"))
        self.assertTrue(should_speak(question, "all"))


if __name__ == "__main__":
    unittest.main()
