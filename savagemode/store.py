"""Savage Mode · L0：块库持久化（data/plugin_data/<plugin>/prompts.json）。

- 原子写：临时文件 + os.replace，避免写一半崩掉丢数据；
- 热加载：文件被手工改动（mtime 变化）时自动重读；
- 宽容解析：坏行丢弃而不是抛错。
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile

from .l0 import Block, normalize_blocks, now_ts

DATA_VERSION = 2


class BlockStore:
    def __init__(self, path: pathlib.Path):
        self.path = pathlib.Path(path)
        self._blocks: list[Block] = []
        self._mtime: float | None = None

    # -- 读 ------------------------------------------------------------

    def load(self, force: bool = False) -> list[Block]:
        mtime = self._file_mtime()
        if not force and mtime is not None and mtime == self._mtime:
            return [Block(**block.to_dict()) for block in self._blocks]
        self._blocks = self._read_file()
        self._mtime = mtime
        return [Block(**block.to_dict()) for block in self._blocks]

    def _read_file(self) -> list[Block]:
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        blocks = raw.get("blocks") if isinstance(raw, dict) else raw
        return normalize_blocks(blocks)

    def _file_mtime(self) -> float | None:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    # -- 写 ------------------------------------------------------------

    def save(self, raw_blocks) -> list[Block]:
        blocks = normalize_blocks(raw_blocks)
        payload = {
            "version": DATA_VERSION,
            "updated_at": now_ts(),
            "blocks": [block.to_dict() for block in blocks],
        }
        self._write_atomic(json.dumps(payload, ensure_ascii=False, indent=2))
        self._blocks = blocks
        self._mtime = self._file_mtime()
        return [Block(**block.to_dict()) for block in blocks]

    def _write_atomic(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
