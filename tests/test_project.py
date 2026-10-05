import unittest

from meeting_copilot.project import TextProjectContext


class ProjectTests(unittest.TestCase):
    def test_relevant_requirement_near_end_of_document_is_retrieved(self):
        project = TextProjectContext(
            ("Routine frontend styling details. " * 250) + "\n\nConstraint: MongoDB must not be introduced."
        )
        result = project.retrieve("Let us use MongoDB for events.", limit=4000)
        self.assertIn("MongoDB must not", result)
        self.assertLessEqual(len(result), 4000)


if __name__ == "__main__":
    unittest.main()
