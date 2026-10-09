import pytest

from vmonitor.keys import Combo, KeyError_, normalize_key, parse_combo, parse_key_sequence, split_modifier_string


@pytest.mark.parametrize("text,expected", [
    ("ctrl+c", Combo("c", ("ctrl",))),
    ("Control+S", Combo("s", ("ctrl",))),
    ("Return", Combo("enter")),
    ("alt+Tab", Combo("tab", ("alt",))),
    ("ctrl+shift+T", Combo("t", ("ctrl", "shift"))),
    ("shift+ctrl+t", Combo("t", ("ctrl", "shift"))),
    ("ctrl++", Combo("+", ("ctrl",))),
    ("+", Combo("+")),
    ("A", Combo("a", ("shift",))),
    ("a", Combo("a")),
    ("Page_Down", Combo("pagedown")),
    ("super", Combo("meta")),
    ("cmd+space", Combo("space", ("meta",))),
    ("KeyA", Combo("a")),
    ("ArrowLeft", Combo("left")),
    ("F12", Combo("f12")),
    ("shift", Combo("shift")),
    ("Escape", Combo("esc")),
])
def test_parse_combo(text, expected):
    assert parse_combo(text) == expected


def test_sequence_and_space():
    assert [str(c) for c in parse_key_sequence("ctrl+a BackSpace")] == ["ctrl+a", "backspace"]
    assert parse_key_sequence(" ") == [Combo("space")]


def test_modifier_string():
    assert split_modifier_string("shift+ctrl") == ("ctrl", "shift")
    assert split_modifier_string(None) == ()
    with pytest.raises(KeyError_):
        split_modifier_string("ctrl+a")


def test_unknown_key():
    with pytest.raises(KeyError_):
        normalize_key("notakey")
    with pytest.raises(KeyError_):
        parse_combo("a+b")
