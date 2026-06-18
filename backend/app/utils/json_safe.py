from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def to_json_safe(value: Any) -> Any:
    """Recursively convert values to JSON-serializable Python types."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (str, int, bool)):
        return value

    type_name = type(value).__name__
    if type_name in {"int64", "int32", "int8", "uint64", "uint32", "float64", "float32"}:
        return value.item() if hasattr(value, "item") else int(value) if "int" in type_name else float(value)
    if type_name == "bool_":
        return bool(value)
    if type_name == "Timestamp":
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    if hasattr(value, "_mapping"):
        return to_json_safe(dict(value._mapping))
    if hasattr(value, "_asdict"):
        return to_json_safe(value._asdict())

    if isinstance(value, dict):
        return {str(key): to_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_safe(item) for item in value]

    return str(value)
