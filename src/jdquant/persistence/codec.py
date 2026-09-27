"""Lossless JSON encoding of domain dataclasses (Decimals as strings, UTC ISO timestamps)."""

from __future__ import annotations

import dataclasses
import types
import typing
from collections import deque
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from functools import cache
from typing import Any, TypeVar

T = TypeVar("T")


def encode(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset | deque):
        return [encode(v) for v in value]
    raise TypeError(f"cannot encode {type(value).__name__}")


@cache
def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def decode(cls: type[T], data: Any) -> T:
    return _decode(cls, data)


def _decode(tp: Any, data: Any) -> Any:
    if data is None:
        return None
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        return _decode(non_none[0], data) if len(non_none) == 1 else data
    if origin in (list, set, frozenset, deque):
        return origin(_decode(args[0], v) for v in data) if args else origin(data)
    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode(args[0], v) for v in data)
        return tuple(_decode(a, v) for a, v in zip(args, data, strict=True))
    if origin is dict:
        return {k: _decode(args[1], v) for k, v in data.items()} if args else dict(data)
    if tp is Any or tp is object:
        return data
    if isinstance(tp, type):
        if issubclass(tp, Enum):
            return tp(data)
        if tp is Decimal:
            return Decimal(data)
        if tp is datetime:
            return datetime.fromisoformat(data)
        if tp is date:
            return date.fromisoformat(data)
        if dataclasses.is_dataclass(tp):
            hints = _hints(tp)
            kwargs = {
                f.name: _decode(hints[f.name], data[f.name])
                for f in dataclasses.fields(tp)
                if f.name in data and f.init
            }
            return tp(**kwargs)
    return data
