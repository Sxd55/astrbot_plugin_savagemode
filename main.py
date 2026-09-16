"""Savage Mode · L0：把长期稳定的系统提示词注入 ProviderRequest.system_prompt。

职责边界见 CLAUDE.md：
- 只做 L0（稳定层），不碰动态内容 / 上下文 / 人格接管；
- 幂等注入、缓存友好、异常不阻断请求；
- 编辑入口：WebUI 插件页的 Savage Mode 面板（块库） + 插件配置页（基础文本/开关）。
"""

from __future__ import annotations

import pathlib
import time
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.api.web import error_response, json_response, request

try:
    from .savagemode import PLUGIN_NAME, __version__
    from .savagemode.l0 import (
        MAX_BLOCKS,
        MAX_BLOCK_CHARS,
        MAX_TOTAL_CHARS,
        InjectionRecord,
        L0Options,
        ModeState,
        block_lines,
        build_block,
        estimate_tokens,
        fingerprint,
        inject,
        normalize_blocks,
        prefix_fingerprint,
        preview_text,
        status_lines,
        studio_stats,
    )
    from .savagemode.store import BlockStore
except ImportError:  # 以插件目录为工作目录加载时
    from savagemode import PLUGIN_NAME, __version__
    from savagemode.l0 import (
        MAX_BLOCKS,
        MAX_BLOCK_CHARS,
        MAX_TOTAL_CHARS,
        InjectionRecord,
        L0Options,
        ModeState,
        block_lines,
        build_block,
        estimate_tokens,
        fingerprint,
        inject,
        normalize_blocks,
        prefix_fingerprint,
        preview_text,
        status_lines,
        studio_stats,
    )
    from savagemode.store import BlockStore

DATA_DIR_NAME = "plugin_data"


def _data_dir() -> pathlib.Path:
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        root = pathlib.Path(get_astrbot_data_path())
    except Exception:  # noqa: BLE001
        root = pathlib.Path("data")
    path = root / DATA_DIR_NAME / PLUGIN_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


@register(
    PLUGIN_NAME,
    "Sxd55",
    "Savage Mode · L0：把长期稳定的角色设定与规则注入系统提示词；可视块库、缓存友好、可预览、可自检。",
    __version__,
)
class SavageModePlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self.state = ModeState()
        self.store = BlockStore(_data_dir() / "prompts.json")
        self._register_pages()
        logger.info("Savage Mode (L0) loaded v%s", __version__)

    def _register_pages(self) -> None:
        apis = [
            ("state", self.page_state, ["GET"], "Panel state"),
            ("blocks/save", self.page_blocks_save, ["POST"], "Save block library"),
            ("settings/save", self.page_settings_save, ["POST"], "Save settings"),
            ("blocks/preview", self.page_blocks_preview, ["POST"], "Preview unsaved blocks"),
        ]
        for route, handler, methods, desc in apis:
            self.context.register_web_api(f"/{PLUGIN_NAME}/{route}", handler, methods, desc)

    # ---- 组装 / 注入 ------------------------------------------------

    def _options(self) -> L0Options:
        return L0Options.from_config(self.config)

    def _blocks(self):
        return self.store.load()

    def _current_block(self, options: L0Options | None = None) -> str:
        options = options or self._options()
        return build_block(options, options.text, self._blocks())

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """LLM 请求前注入 L0 块（框架拼装完人格/技能之后触发）。"""
        try:
            options = self._options()
            platform = ""
            try:
                platform = event.get_platform_name() or ""
            except Exception:  # noqa: BLE001
                platform = ""
            umo = ""
            try:
                umo = event.unified_msg_origin or ""
            except Exception:  # noqa: BLE001
                umo = ""

            current = req.system_prompt or ""
            known_bodies = self.state.known_bodies(umo)
            block = self._current_block(options)

            if not options.enabled or not options.applies_to(platform) or not block:
                cleaned = inject(current, "", options.position, known_bodies)
                req.system_prompt = cleaned
                if options.debug_log:
                    logger.info(
                        "Savage Mode L0 skipped: enabled=%s platform=%s block_len=%s",
                        options.enabled,
                        platform,
                        len(block),
                    )
                return

            updated = inject(current, block, options.position, known_bodies)
            req.system_prompt = updated

            record = InjectionRecord(
                ts=time.time(),
                umo=umo,
                platform=platform,
                block_fp=fingerprint(block),
                prefix_fp=prefix_fingerprint(updated, block),
                total_len=len(updated),
                total_tokens=estimate_tokens(updated),
                block_tokens=estimate_tokens(block),
            )
            self.state.remember(record, block)
            if options.debug_log:
                logger.info(
                    "Savage Mode L0 injected %s chars (block=%s, total=%s, position=%s)",
                    len(block),
                    record.block_fp,
                    record.total_len,
                    options.position,
                )
        except Exception as exc:  # noqa: BLE001
            # 注入失败也不能影响对话。
            logger.warning("Savage Mode L0 inject failed: %s", exc)

    # ---- 面板 API ----------------------------------------------------

    def _settings(self, options: L0Options) -> dict[str, Any]:
        return {
            "enabled": options.enabled,
            "position": options.position,
            "wrap_tag": options.wrap_tag,
            "platforms": list(options.platforms),
            "base_text": options.text,
            "debug_log": options.debug_log,
        }

    def _cache_payload(self) -> dict[str, Any]:
        umo = self.state.last_umo
        if not umo:
            return {"stable": True, "same": 0, "total": 0, "count": 0, "last_total_len": 0, "last_tokens": 0}
        stable, same, total = self.state.cache_report(umo)
        items = self.state.history(umo)
        last = items[-1] if items else None
        return {
            "stable": stable,
            "same": same,
            "total": total,
            "count": len(items),
            "last_total_len": last.total_len if last else 0,
            "last_tokens": last.total_tokens if last else 0,
        }

    def _state_payload(self, options: L0Options, blocks) -> dict[str, Any]:
        block = build_block(options, options.text, blocks)
        stats = studio_stats(options.text, blocks, options)
        return {
            "version": __version__,
            "settings": self._settings(options),
            "blocks": [item.to_dict() for item in blocks],
            "preview": {
                "body": build_block(options, options.text, blocks),
                "chars": stats["block_chars"],
                "tokens": stats["block_tokens"],
            },
            "stats": stats,
            "cache": self._cache_payload(),
            "limits": {
                "max_blocks": MAX_BLOCKS,
                "max_block_chars": MAX_BLOCK_CHARS,
                "max_total_chars": MAX_TOTAL_CHARS,
            },
            "path": str(self.store.path),
        }

    async def page_state(self):
        return json_response(self._state_payload(self._options(), self._blocks()))

    async def page_blocks_preview(self):
        """预览「尚未保存」的块库，供面板实时显示。"""
        payload = await request.json(default={})
        raw = payload.get("blocks") if isinstance(payload, dict) else None
        if not isinstance(raw, list):
            return error_response("blocks 必须是数组")
        options = self._options()
        blocks = normalize_blocks(raw)
        block = build_block(options, options.text, blocks)
        return json_response(
            {
                "preview": {
                    "body": block,
                    "chars": len(block),
                    "tokens": estimate_tokens(block),
                },
                "stats": studio_stats(options.text, blocks, options),
            }
        )

    async def page_blocks_save(self):
        payload = await request.json(default={})
        raw = payload.get("blocks") if isinstance(payload, dict) else None
        if not isinstance(raw, list):
            return error_response("blocks 必须是数组")
        blocks = self.store.save(raw)
        return json_response(self._state_payload(self._options(), blocks))

    async def page_settings_save(self):
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("请求体必须是对象")
        if "values" in payload and not isinstance(payload["values"], dict):
            return error_response("values 必须是对象")
        values = payload.get("values") if isinstance(payload.get("values"), dict) else payload
        merged = {**self.config, **values}
        parsed = L0Options.from_config(merged)
        self.config["enabled"] = parsed.enabled
        self.config["position"] = parsed.position
        self.config["wrap_tag"] = parsed.wrap_tag
        self.config["platforms"] = list(parsed.platforms)
        self.config["debug_log"] = parsed.debug_log
        self.config["l0_text"] = parsed.text
        saver = getattr(self.config, "save_config", None)
        if callable(saver):
            saver()
        return json_response(self._state_payload(self._options(), self._blocks()))

    # ---- 指令 -------------------------------------------------------

    @filter.command_group("savagemode")
    def savagemode(self):
        """Savage Mode（L0 稳定层）"""

    @filter.permission_type(filter.PermissionType.ADMIN)
    @savagemode.command("status")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看 L0 配置与缓存自检"""
        options = self._options()
        blocks = self._blocks()
        umo = ""
        try:
            umo = event.unified_msg_origin or ""
        except Exception:  # noqa: BLE001
            umo = ""
        report = self.state.cache_report(umo) if umo else (True, 0, 0)
        count = len(self.state.history(umo)) if umo else 0
        stats = studio_stats(options.text, blocks, options)
        yield event.plain_result("\n".join(status_lines(options, __version__, report, count, stats)))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @savagemode.command("preview")
    async def cmd_preview(self, event: AstrMessageEvent):
        """预览即将注入的内容"""
        options = self._options()
        yield event.plain_result(preview_text(options, options.text, self._blocks()))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @savagemode.command("blocks")
    async def cmd_blocks(self, event: AstrMessageEvent):
        """列出注入块（与顺序）"""
        yield event.plain_result("\n".join(block_lines(self._blocks())))

    async def terminate(self):
        self.state.records.clear()
