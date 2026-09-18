"""Savage Mode · L0 纯逻辑：块库渲染 → 幂等注入 → 指纹 / 统计 / 自检。

L0 的定义：注入「长期不变」的系统提示词（角色设定/规则/格式/红线）。
本模块刻意不提供任何模板变量，避免把每轮变化的内容带进 system_prompt
（那会破坏模型服务端的提示词缓存，官方文档点名过：成本约涨 7–20 倍）。
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from dataclasses import dataclass, field

MARKER_TEMPLATE = "<!-- savage-mode:L0:v{fp} -->"
MARKER_RE = re.compile(r"<!--\s*savage-mode:L0:v[0-9a-f]{0,12}\s*-->\s*", re.IGNORECASE)
MARKER_BLOCK_RE = re.compile(
    r"<!--\s*savage-mode:L0:v[0-9a-f]{0,12}\s*-->\s*(?:<([A-Za-z][A-Za-z0-9_-]{0,23})>[\s\S]*?</\1>\s*)?",
    re.IGNORECASE,
)
POSITIONS = ("prepend", "append")
_TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,23}$")

MAX_BLOCKS = 50
MAX_BLOCK_CHARS = 20000
MAX_TOTAL_CHARS = 60000
MAX_TITLE_CHARS = 40
MAX_SESSIONS = 500

_CJK_RANGES = ((0x3040, 0x30FF), (0x4E00, 0x9FFF), (0xAC00, 0xD7AF))


def fingerprint(text: str) -> str:
    """内容指纹（8 位十六进制），用于版本标记与缓存自检。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:8]


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK 约 1 token/字，其它约 4 字符/token。"""
    body = text or ""
    if not body:
        return 0
    cjk = 0
    for char in body:
        code = ord(char)
        if any(low <= code <= high for low, high in _CJK_RANGES):
            cjk += 1
    other = len(body) - cjk
    return cjk + (other + 3) // 4


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_str(value, default: str = "") -> str:
    if value is None:
        return default
    return value if isinstance(value, str) else str(value)


def _as_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_str_list(value) -> list[str]:
    if isinstance(value, str):
        items: list[str] = []
        for line in value.replace("\r", "").split("\n"):
            items.extend(part.strip() for part in line.split(","))
        return [item for item in items if item]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def sanitize_tag(value) -> str:
    """包裹标签只允许安全字符，避免把提示词拼坏。"""
    tag = _as_str(value).strip().strip("<>").strip()
    return tag if _TAG_RE.match(tag) else ""


@dataclass
class L0Options:
    enabled: bool = True
    position: str = "prepend"
    text: str = ""
    platforms: tuple[str, ...] = ()
    wrap_tag: str = "rules"
    debug_log: bool = False

    @classmethod
    def from_config(cls, raw) -> "L0Options":
        data = raw if isinstance(raw, dict) else {}
        position = _as_str(data.get("position"), "prepend").strip().lower()
        if position not in POSITIONS:
            position = "prepend"
        raw_tag = data.get("wrap_tag")
        tag = "rules" if raw_tag is None else sanitize_tag(raw_tag)
        return cls(
            enabled=_as_bool(data.get("enabled"), True),
            position=position,
            text=_as_str(data.get("l0_text")),
            platforms=tuple(_as_str_list(data.get("platforms"))),
            wrap_tag=tag,
            debug_log=_as_bool(data.get("debug_log"), False),
        )

    def applies_to(self, platform: str) -> bool:
        """是否应在本平台注入（文本与块库都为空时视为不注入）。"""
        if not self.enabled:
            return False
        return not self.platforms or platform in self.platforms


@dataclass
class Block:
    id: str = ""
    title: str = ""
    text: str = ""
    enabled: bool = True
    sort: int = 0

    @classmethod
    def from_raw(cls, raw, index: int = 0) -> "Block":
        data = raw if isinstance(raw, dict) else {"text": raw}
        block_id = _as_str(data.get("id")).strip()[:32] or uuid.uuid4().hex[:8]
        title = _as_str(data.get("title")).strip()[:MAX_TITLE_CHARS]
        text = _as_str(data.get("text"))[:MAX_BLOCK_CHARS]
        return cls(
            id=block_id,
            title=title,
            text=text,
            enabled=_as_bool(data.get("enabled"), True),
            sort=_as_int(data.get("sort"), index),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "text": self.text,
            "enabled": self.enabled,
            "sort": self.sort,
        }


def normalize_blocks(raw_blocks) -> list[Block]:
    """解析 + 清洗 + 排序，重排 sort 连续；全空块丢弃。"""
    blocks: list[Block] = []
    for index, raw in enumerate(raw_blocks or []):
        block = Block.from_raw(raw, index)
        if block.text.strip() or block.title.strip():
            blocks.append(block)
    blocks.sort(key=lambda item: (item.sort, item.id))
    for order, block in enumerate(blocks):
        block.sort = order
    return blocks[:MAX_BLOCKS]


def active_blocks(blocks) -> list[Block]:
    return [block for block in blocks if block.enabled and block.text.strip()]


def render_parts(base_text: str, blocks) -> list[dict]:
    """按顺序给出参与注入的片段（基础文本在前，然后是启用的块）。"""
    parts: list[dict] = []
    base = (base_text or "").strip()
    if base:
        parts.append({"id": "base", "title": "基础文本", "text": base, "source": "config"})
    for block in active_blocks(blocks):
        parts.append(
            {
                "id": block.id,
                "title": block.title or "未命名块",
                "text": block.text.strip(),
                "source": "block",
            }
        )
    return parts


def compose_body(base_text: str, blocks) -> str:
    body = "\n\n".join(part["text"] for part in render_parts(base_text, blocks))
    if len(body) > MAX_TOTAL_CHARS:
        body = body[:MAX_TOTAL_CHARS]
    return body


def build_block(options: L0Options, base_text: str = "", blocks=()) -> str:
    """组装最终注入块：标记 + 可选包裹 + 正文。空正文返回空串。"""
    body = compose_body(base_text, blocks)
    if not body:
        return ""
    wrapped = f"<{options.wrap_tag}>\n{body}\n</{options.wrap_tag}>" if options.wrap_tag else body
    return f"{MARKER_TEMPLATE.format(fp=fingerprint(wrapped))}\n{wrapped}"


def strip_previous(system_prompt: str, known_bodies: tuple[str, ...] = ()) -> str:
    """清掉本插件上一版残留（旧正文 + 标记），并压掉多余空行。"""
    text = system_prompt or ""
    for body in known_bodies:
        if body and body in text:
            text = text.replace(body, "")
    # 先用组合正则匹配标记及紧随其后的整个标签包裹块（防机器人重启后无 known_bodies 残留旧标签）
    text = MARKER_BLOCK_RE.sub("", text)
    text = MARKER_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def inject(
    system_prompt: str,
    block: str,
    position: str = "prepend",
    known_bodies: tuple[str, ...] = (),
) -> str:
    """把块幂等地注入 system_prompt；position=prepend/append。

    幂等靠三件事：先删同款块、再删历史正文、最后清标记。
    """
    bodies = tuple(known_bodies) + ((block,) if block else ())
    cleaned = strip_previous(system_prompt, bodies)
    if not block:
        return cleaned
    if position == "append":
        return f"{cleaned}\n\n{block}".strip() if cleaned else block
    return f"{block}\n\n{cleaned}".strip() if cleaned else block


def prefix_fingerprint(system_prompt: str, block: str) -> str:
    """去掉本插件注入块后的「框架部分」指纹，用于缓存自检。"""
    text = system_prompt or ""
    if block and block in text:
        text = text.replace(block, "")
    return fingerprint(text.strip())


def studio_stats(base_text: str, blocks, options: L0Options) -> dict:
    """面板/指令用的统计：每段字数与 token、合计、整块。"""
    parts = render_parts(base_text, blocks)
    for part in parts:
        part["chars"] = len(part["text"])
        part["tokens"] = estimate_tokens(part["text"])
    block = build_block(options, base_text, blocks)
    return {
        "parts": parts,
        "total_chars": sum(part["chars"] for part in parts),
        "total_tokens": sum(part["tokens"] for part in parts),
        "block_chars": len(block),
        "block_tokens": estimate_tokens(block),
        "block_count": len(parts),
    }


@dataclass
class InjectionRecord:
    ts: float
    umo: str
    platform: str
    block_fp: str
    prefix_fp: str
    total_len: int
    total_tokens: int = 0
    block_tokens: int = 0


@dataclass
class ModeState:
    """内存态：最近注入快照 + 缓存自检（不落盘，重启即清）。"""

    records: dict[str, list[InjectionRecord]] = field(default_factory=dict)
    last_block: str = ""
    last_bodies: dict[str, str] = field(default_factory=dict)
    last_umo: str = ""
    limit: int = 12
    max_sessions: int = MAX_SESSIONS

    def remember(self, record: InjectionRecord, block: str) -> None:
        if record.umo not in self.records and len(self.records) >= self.max_sessions:
            oldest_umo = next(iter(self.records))
            self.records.pop(oldest_umo, None)
            self.last_bodies.pop(oldest_umo, None)
        items = self.records.setdefault(record.umo, [])
        items.append(record)
        del items[: max(0, len(items) - self.limit)]
        self.last_block = block
        self.last_bodies[record.umo] = block
        self.last_umo = record.umo

    def history(self, umo: str) -> list[InjectionRecord]:
        return list(self.records.get(umo) or [])

    def known_bodies(self, umo: str) -> tuple[str, ...]:
        body = self.last_bodies.get(umo)
        return (body,) if body else ()

    def cache_report(self, umo: str) -> tuple[bool, int, int]:
        """返回 (框架前缀是否稳定, 一致次数, 样本数)。"""
        items = self.history(umo)
        if len(items) < 2:
            return True, len(items), len(items)
        latest = items[-1].prefix_fp
        same = sum(1 for item in items if item.prefix_fp == latest)
        return same == len(items), same, len(items)


def status_lines(
    options: L0Options,
    version: str,
    report: tuple[bool, int, int],
    count: int,
    block_stats: dict | None = None,
) -> list[str]:
    """生成 /savagemode status 的正文（纯函数，便于测试）。"""
    stats_data = block_stats or {}
    stable, same, total = report
    tokens = stats_data.get("block_tokens", 0)
    parts = stats_data.get("block_count", 0)
    lines = [
        f"Savage Mode · L0 v{version}",
        f"开关 {'开' if options.enabled else '关'} / 位置 {'前置' if options.position == 'prepend' else '追加'}"
        f" / 平台 {'全部' if not options.platforms else '、'.join(options.platforms)}",
        f"包裹 {'<' + options.wrap_tag + '>' if options.wrap_tag else '无'}",
        f"基础文本 {len(options.text.strip())} 字 / 注入片段 {parts} 个 / 预估 {tokens} tokens",
        f"最近注入 {count} 次",
        f"缓存自检 框架前缀 {'稳定' if stable else '有变化'}"
        + (f"（{same}/{total} 一致）" if total else "（样本不足）"),
    ]
    return lines


def preview_text(options: L0Options, base_text: str, blocks, limit: int = 600) -> str:
    """生成 /savagemode preview 的正文。"""
    block = build_block(options, base_text, blocks)
    if not block:
        return "当前配置与块库都是空的，不会注入任何内容。"
    body = block if len(block) <= limit else block[: limit - 1] + "…"
    return f"将注入 {len(block)} 字 / 约 {estimate_tokens(block)} tokens（{options.position}）：\n\n{body}"


def block_lines(blocks) -> list[str]:
    """块库一览（指令用）。"""
    if not blocks:
        return ["块库为空。在 WebUI 插件页的 Savage Mode 面板里添加。"]
    lines = ["块库（按注入顺序）："]
    for block in blocks:
        flag = "开" if block.enabled else "关"
        title = block.title or "未命名块"
        lines.append(f"  [{flag}] {title} · {len(block.text.strip())} 字 · 约 {estimate_tokens(block.text)} tokens")
    return lines


def now_ts() -> float:
    return time.time()
