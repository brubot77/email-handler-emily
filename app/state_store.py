from __future__ import annotations

import json
from pathlib import Path


class StateStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(json.dumps({"processed_message_ids": []}, indent=2), encoding="utf-8")

    def load(self) -> set[str]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return set(data.get("processed_message_ids", []))

    def save(self, processed_ids: set[str]) -> None:
        self.path.write_text(
            json.dumps({"processed_message_ids": sorted(processed_ids)}, indent=2),
            encoding="utf-8",
        )