"""Best-effort removal of personal identifiers from report text."""
from __future__ import annotations

import os
import pathlib
import platform
import re

_IDENT_LINE = re.compile(
    r'(?im)^.*(?:serial number|serial_number|\bserial\b|wwn|eui64|nguid|mac address|'
    r'host name|hostname|product_uuid).*$')
_GENERIC_USERS = {'deck', 'root', 'user', 'steam'}
_GENERIC_HOSTS = {'steamdeck', 'localhost'}
_MAC = re.compile(r'\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b', re.I)


class Redactor:
    def __init__(self, home: str | None = None, users: list | None = None, hostname: str | None = None):
        self.home = home if home is not None else str(pathlib.Path.home())
        if users is None:
            users = [os.environ.get('USER', ''), os.environ.get('LOGNAME', '')]
        # 'deck' is the SteamOS default account: not personal, and redacting it would
        # mangle words like "deck-integrity".
        self.users = sorted({u for u in users if u and len(u) > 2 and u not in _GENERIC_USERS},
                            key=len, reverse=True)
        self.hostname = hostname if hostname is not None else platform.node()
        if self.hostname in _GENERIC_HOSTS:   # SteamOS default; also its VARIANT_ID
            self.hostname = ''

    def __call__(self, text) -> str:
        s = str(text)
        if self.home and len(self.home) > 1:
            s = s.replace(self.home, '$HOME')
        if self.hostname and len(self.hostname) > 2:
            s = re.sub(r'(?<![\w-])' + re.escape(self.hostname) + r'(?![\w-])', '<host>', s)
        for u in self.users:
            s = re.sub(r'(?<![\w-])' + re.escape(u) + r'(?![\w-])', '<user>', s)
        s = _IDENT_LINE.sub('<identifier redacted>', s)
        s = _MAC.sub('<mac redacted>', s)
        return s
