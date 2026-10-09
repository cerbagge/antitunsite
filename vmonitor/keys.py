"""키 이름 정규화.

AI·매크로마다 키 이름 표기가 다릅니다(xdotool: ``Return``, ``ctrl+s`` / 웹: ``Enter``,
``Control`` / 사람: ``엔터``가 아닌 ``enter`` 등). 여기서 모든 표기를 하나의 '정규 키 이름'으로
바꾸고, 각 백엔드는 정규 이름만 자기 방식(Playwright 키, Win32 VK, X11 keysym)으로 번역합니다.

정규 키 이름
  - 수정자: ``shift`` ``ctrl`` ``alt`` ``meta`` (meta = Windows/Command/Super 키)
  - 이름 있는 키: ``enter`` ``esc`` ``tab`` ``backspace`` ``delete`` ``insert`` ``home`` ``end``
    ``pageup`` ``pagedown`` ``up`` ``down`` ``left`` ``right`` ``space`` ``capslock`` ``numlock``
    ``scrolllock`` ``printscreen`` ``pause`` ``contextmenu`` ``hangul`` ``hanja`` ``f1``~``f24``
  - 그 밖의 한 글자: 영문 소문자·숫자·기호 그대로 (예: ``a`` ``1`` ``/``)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MODIFIERS = ("ctrl", "alt", "shift", "meta")

NAMED_KEYS = {
    "enter", "esc", "tab", "backspace", "delete", "insert", "home", "end",
    "pageup", "pagedown", "up", "down", "left", "right", "space",
    "capslock", "numlock", "scrolllock", "printscreen", "pause", "contextmenu",
    "hangul", "hanja",
    *(f"f{i}" for i in range(1, 25)),
    *MODIFIERS,
}

ALIASES = {
    # 수정자
    "control": "ctrl", "ctl": "ctrl", "control_l": "ctrl", "control_r": "ctrl", "lctrl": "ctrl", "rctrl": "ctrl",
    "shift_l": "shift", "shift_r": "shift", "lshift": "shift", "rshift": "shift",
    "alt_l": "alt", "alt_r": "alt", "option": "alt", "opt": "alt", "menu_alt": "alt",
    "cmd": "meta", "command": "meta", "super": "meta", "super_l": "meta", "super_r": "meta",
    "win": "meta", "windows": "meta", "meta_l": "meta", "meta_r": "meta", "os": "meta", "lwin": "meta", "rwin": "meta",
    # 이름 있는 키
    "return": "enter", "kp_enter": "enter", "ret": "enter", "newline": "enter",
    "escape": "esc",
    "del": "delete", "kp_delete": "delete",
    "back_space": "backspace", "bksp": "backspace", "bs": "backspace",
    "ins": "insert",
    "pgup": "pageup", "page_up": "pageup", "prior": "pageup",
    "pgdn": "pagedown", "page_down": "pagedown", "next": "pagedown",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "kp_up": "up", "kp_down": "down", "kp_left": "left", "kp_right": "right",
    "kp_home": "home", "kp_end": "end",
    "spacebar": "space",
    "caps_lock": "capslock", "num_lock": "numlock", "scroll_lock": "scrolllock",
    "print": "printscreen", "prtsc": "printscreen", "prtscr": "printscreen", "print_screen": "printscreen",
    "menu": "contextmenu", "apps": "contextmenu", "context_menu": "contextmenu",
    "hangul_hanja": "hanja", "hangulmode": "hangul", "hanjamode": "hanja", "kana": "hangul",
    # 기호 이름(xdotool keysym 표기)
    "minus": "-", "plus": "+", "equal": "=", "comma": ",", "period": ".", "slash": "/",
    "backslash": "\\", "semicolon": ";", "apostrophe": "'", "quote": "'", "quotedbl": '"',
    "grave": "`", "backquote": "`", "bracketleft": "[", "bracketright": "]",
    "braceleft": "{", "braceright": "}", "parenleft": "(", "parenright": ")",
    "exclam": "!", "at": "@", "numbersign": "#", "dollar": "$", "percent": "%",
    "asciicircum": "^", "ampersand": "&", "asterisk": "*", "underscore": "_",
    "colon": ":", "less": "<", "greater": ">", "question": "?", "bar": "|", "asciitilde": "~",
}

# US 배열 기준: Shift 를 눌러야 나오는 기호 → (기본 키)
SHIFTED_SYMBOLS = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
    "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\", ":": ";",
    '"': "'", "<": ",", ">": ".", "?": "/", "~": "`",
}


class KeyError_(ValueError):
    """알 수 없는 키 이름."""


def normalize_key(name: str) -> str:
    """키 하나의 이름을 정규 이름으로 바꿉니다. 대소문자는 구분하지 않습니다(한 글자 제외)."""
    if not isinstance(name, str) or name == "":
        raise KeyError_(f"키 이름이 비어 있습니다: {name!r}")
    if name == " ":
        return "space"
    if len(name) == 1:
        return name.lower() if name.isalpha() and name.isascii() else name
    low = name.strip().lower().replace("-", "_") if len(name.strip()) > 1 else name.strip()
    if low in NAMED_KEYS:
        return low
    if low in ALIASES:
        return ALIASES[low]
    compact = low.replace("_", "")
    if compact in NAMED_KEYS:
        return compact
    if compact in ALIASES:
        return ALIASES[compact]
    # 웹 KeyboardEvent.code 형식 (KeyA, Digit1)
    m = re.fullmatch(r"key([a-z])", compact)
    if m:
        return m.group(1)
    m = re.fullmatch(r"(?:digit|kp_?|numpad)([0-9])", compact)
    if m:
        return m.group(1)
    raise KeyError_(f"알 수 없는 키 이름입니다: {name!r}")


@dataclass(frozen=True)
class Combo:
    """수정자 + 키 하나로 이루어진 단축키. 예) ctrl+shift+t"""

    key: str
    modifiers: tuple[str, ...] = field(default_factory=tuple)

    def __str__(self) -> str:
        return "+".join([*self.modifiers, self.key])


def _split_combo(text: str) -> list[str]:
    # "ctrl++" → ["ctrl", "+"],  "+" → ["+"],  "a+b" → ["a", "b"]
    return re.split(r"(?<!^)(?<!\+)\+(?!$)", text)


def parse_combo(text: str) -> Combo:
    """``ctrl+shift+t`` / ``Control+S`` / ``alt+Tab`` / ``A`` 같은 문자열을 Combo 로 해석합니다."""
    if text is None or text == "":
        raise KeyError_("키 조합이 비어 있습니다")
    parts = _split_combo(text) if text != " " else [" "]
    names = [normalize_key(p) if p != "" else "+" for p in parts]
    mods: list[str] = []
    key: str | None = None
    for raw, n in zip(parts, names):
        if n in MODIFIERS and (key is None or n not in mods):
            # 마지막 토큰이 수정자 하나뿐이면(예: "shift") 그 자체가 키
            mods.append(n)
        else:
            if key is not None:
                raise KeyError_(f"키 조합에는 수정자가 아닌 키가 하나만 와야 합니다: {text!r}")
            key = n
            # 단독 대문자 'A' 는 shift+a 로 해석
            if len(raw) == 1 and raw.isalpha() and raw.isascii() and raw.isupper() and "shift" not in mods and len(parts) == 1:
                mods.append("shift")
    if key is None:
        # "ctrl" 처럼 수정자만 있는 경우: 마지막 수정자를 키로 취급
        key = mods.pop()
    ordered = tuple(m for m in MODIFIERS if m in mods)
    return Combo(key=key, modifiers=ordered)


def parse_key_sequence(text: str) -> list[Combo]:
    """공백으로 구분된 여러 조합(xdotool 방식)을 순서대로 해석합니다. 예) ``ctrl+a BackSpace``"""
    if text.strip() == "" and text != "":
        return [Combo("space")]
    tokens = text.split()
    if len(tokens) <= 1:
        return [parse_combo(text.strip() or text)]
    return [parse_combo(t) for t in tokens]


def split_modifier_string(text: str | None) -> tuple[str, ...]:
    """``"ctrl+shift"`` 같은 수정자 문자열을 튜플로. 클릭·스크롤에 함께 누를 키에 사용합니다."""
    if not text:
        return ()
    mods = []
    for p in _split_combo(text):
        n = normalize_key(p)
        if n not in MODIFIERS:
            raise KeyError_(f"수정자 키가 아닙니다: {p!r}")
        if n not in mods:
            mods.append(n)
    return tuple(m for m in MODIFIERS if m in mods)
