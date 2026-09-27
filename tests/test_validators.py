"""
tests/test_validators.py

Unit tests for minichain.validators.is_valid_receiver.
"""

from minichain.validators import is_valid_receiver


def test_valid_40_char_hex_receiver():
    assert is_valid_receiver("a" * 40) is True


def test_valid_64_char_hex_receiver():
    assert is_valid_receiver("f" * 64) is True


def test_invalid_length_receiver_rejected():
    assert is_valid_receiver("a" * 41) is False


def test_non_hex_characters_rejected():
    assert is_valid_receiver("z" * 40) is False


def test_empty_receiver_rejected():
    assert is_valid_receiver("") is False
