import unittest

from jn80_librarian.position import WritePosition, increment_position


class TestPosition(unittest.TestCase):
    def test_increment_inside_bank(self) -> None:
        self.assertEqual(increment_position(WritePosition("A", 1)), WritePosition("A", 2))

    def test_increment_wrap_slot(self) -> None:
        self.assertEqual(increment_position(WritePosition("A", 20)), WritePosition("B", 1))

    def test_increment_wrap_bank(self) -> None:
        self.assertEqual(increment_position(WritePosition("T", 20)), WritePosition("A", 1))


if __name__ == "__main__":
    unittest.main()
