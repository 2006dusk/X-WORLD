"""角色卡包对外接口。"""

from __future__ import annotations

from .loader import (
    card_text_fields,
    delete_card,
    export_card_json,
    export_card_markdown,
    get_card,
    import_card_file,
    import_card_json,
    list_cards,
    read_json_card,
    save_card,
)
from .spec import V2_COMMON_FIELDS, parse_card, v2_text_fields

__all__ = [
    "V2_COMMON_FIELDS",
    "card_text_fields",
    "delete_card",
    "export_card_json",
    "export_card_markdown",
    "get_card",
    "import_card_file",
    "import_card_json",
    "list_cards",
    "parse_card",
    "read_json_card",
    "save_card",
    "v2_text_fields",
]
