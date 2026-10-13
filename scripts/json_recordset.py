"""Read a catalogue JSON object without buffering its entire serialized text.

The records array is decoded one row at a time. A transform can consume/discard
rows, permitting streaming audits while preserving metadata before or after it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, TextIO


def interning_object_hook() -> Callable:
    """Share repeated keys and string values across separately decoded records."""
    strings: dict[str, str] = {}

    def hook(pairs: list[tuple[str, Any]]) -> dict:
        return {strings.setdefault(key, key): strings.setdefault(value, value)
                if isinstance(value, str) else value for key, value in pairs}

    return hook


class _Reader:
    def __init__(self, handle: TextIO, decoder: json.JSONDecoder, chunk_size: int, max_value_chars: int):
        self.handle, self.decoder = handle, decoder
        self.chunk_size, self.max_value_chars = chunk_size, max_value_chars
        self.buffer, self.pos, self.eof = "", 0, False

    def fill(self) -> None:
        self.buffer = self.buffer[self.pos:]
        self.pos = 0
        chunk = self.handle.read(self.chunk_size)
        self.buffer += chunk
        self.eof = not chunk
        if len(self.buffer) > self.max_value_chars:
            raise ValueError("Catalogue value exceeds the streaming size limit")

    def peek(self) -> str:
        while True:
            while self.pos < len(self.buffer) and self.buffer[self.pos] in " \t\r\n":
                self.pos += 1
            if self.pos < len(self.buffer):
                return self.buffer[self.pos]
            if self.eof:
                return ""
            self.fill()

    def expect(self, char: str) -> None:
        if self.peek() != char:
            raise ValueError(f"Expected {char!r} in catalogue JSON")
        self.pos += 1

    def value(self) -> Any:
        self.peek()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.pos)
            except json.JSONDecodeError:
                if self.eof:
                    raise
                self.fill()
                continue
            # A scalar may continue in the next chunk (for example 12 + 34).
            if end == len(self.buffer) and not self.eof:
                self.fill()
                continue
            self.pos = end
            return value


def load_json_recordset(path: str | Path, *, object_pairs_hook: Callable | None = None,
                       record_transform: Callable[[dict], dict | None] | None = None,
                       record_keys: tuple[str, ...] = ("records",),
                       allow_root_array: bool = False, strict_records: bool = True,
                       chunk_size: int = 1024 * 1024,
                       max_value_chars: int = 64 * 1024 * 1024) -> dict:
    """Load top-level metadata and records; transform=None retains every row.

    A transform returning None consumes a row without retaining it. Invalid
    structure, trailing content and duplicate top-level keys fail explicitly.
    Legacy audit inputs can opt into alternate array keys or a root array;
    catalogue callers retain strict object and record validation by default.
    """
    if not 1 <= chunk_size <= max_value_chars:
        raise ValueError("Invalid streaming buffer bounds")
    decoder = json.JSONDecoder(object_pairs_hook=object_pairs_hook)
    payload: dict = {}
    with Path(path).open(encoding="utf-8") as handle:
        reader = _Reader(handle, decoder, chunk_size, max_value_chars)

        def read_records() -> list:
            reader.expect("[")
            rows = []
            if reader.peek() != "]":
                while True:
                    row = reader.value()
                    if strict_records and not isinstance(row, dict):
                        raise ValueError("Catalogue records must be objects")
                    row = record_transform(row) if record_transform else row
                    if row is not None:
                        rows.append(row)
                    if reader.peek() == "]":
                        break
                    reader.expect(",")
            reader.expect("]")
            return rows

        if allow_root_array and reader.peek() == "[":
            payload["records"] = read_records()
            if reader.peek():
                raise ValueError("Unexpected content after catalogue JSON")
            return payload
        reader.expect("{")
        if reader.peek() != "}":
            while True:
                key = reader.value()
                if not isinstance(key, str) or key in payload:
                    raise ValueError("Catalogue object requires unique string keys")
                reader.expect(":")
                if key in record_keys:
                    payload[key] = read_records()
                else:
                    payload[key] = reader.value()
                if reader.peek() == "}":
                    break
                reader.expect(",")
        reader.expect("}")
        if reader.peek():
            raise ValueError("Unexpected content after catalogue JSON")
    return payload
