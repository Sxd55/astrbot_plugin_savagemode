"""Savage Mode · L0 纯逻辑测试（不依赖 astrbot）。

    <venv>/Scripts/python tests/test_core.py -v
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from savagemode.l0 import (  # noqa: E402
    MARKER_RE,
    MAX_BLOCK_CHARS,
    MAX_BLOCKS,
    Block,
    L0Options,
    ModeState,
    InjectionRecord,
    block_lines,
    build_block,
    compose_body,
    estimate_tokens,
    fingerprint,
    inject,
    normalize_blocks,
    prefix_fingerprint,
    preview_text,
    render_parts,
    sanitize_tag,
    status_lines,
    studio_stats,
    strip_previous,
)
from savagemode.store import BlockStore  # noqa: E402


def opts(**overrides) -> L0Options:
    base = {
        "enabled": True,
        "position": "prepend",
        "text": "",
        "platforms": (),
        "wrap_tag": "rules",
        "debug_log": False,
    }
    base.update(overrides)
    return L0Options(**base)


def blocks(*texts, enabled=True):
    return [
        Block(id=f"b{i}", title=f"块{i}", text=text, enabled=enabled, sort=i)
        for i, text in enumerate(texts)
    ]


class OptionsTest(unittest.TestCase):
    def test_defaults(self):
        options = L0Options.from_config({})
        self.assertTrue(options.enabled)
        self.assertEqual(options.position, "prepend")
        self.assertEqual(options.wrap_tag, "rules")
        self.assertEqual(options.platforms, ())
        self.assertEqual(options.text, "")

    def test_bad_position_falls_back(self):
        self.assertEqual(L0Options.from_config({"position": "middle"}).position, "prepend")
        self.assertEqual(L0Options.from_config({"position": "APPEND"}).position, "append")

    def test_tag_sanitize(self):
        self.assertEqual(sanitize_tag("rules"), "rules")
        self.assertEqual(sanitize_tag("<rules>"), "rules")
        self.assertEqual(sanitize_tag("bad tag!"), "")
        self.assertEqual(sanitize_tag("a" * 30), "")
        self.assertEqual(L0Options.from_config({"wrap_tag": ""}).wrap_tag, "")

    def test_platforms_and_applies(self):
        options = L0Options.from_config({"platforms": "a, b\nc"})
        self.assertEqual(options.platforms, ("a", "b", "c"))
        self.assertTrue(options.applies_to("a"))
        self.assertFalse(options.applies_to("z"))
        self.assertFalse(L0Options.from_config({"enabled": False}).applies_to("a"))
        self.assertTrue(L0Options.from_config({}).applies_to("anything"))


class BlockTest(unittest.TestCase):
    def test_normalize_sorts_and_compacts(self):
        raw = [
            {"title": "b", "text": "第二", "sort": 5},
            {"title": "a", "text": "第一", "sort": 1},
            {},  # 完全空：丢弃
            {"title": "只有标题", "text": "  ", "sort": 9},  # 有标题没正文：保留（别吃掉用户正在写的块）
        ]
        result = normalize_blocks(raw)
        self.assertEqual([item.title for item in result], ["a", "b", "只有标题"])
        self.assertEqual([item.sort for item in result], [0, 1, 2])

    def test_normalize_generates_id_and_limits(self):
        result = normalize_blocks([{"text": "x"}])
        self.assertTrue(result[0].id)
        many = normalize_blocks([{"text": "x"} for _ in range(MAX_BLOCKS + 10)])
        self.assertEqual(len(many), MAX_BLOCKS)

    def test_block_text_is_clamped(self):
        block = Block.from_raw({"text": "字" * (MAX_BLOCK_CHARS + 100)})
        self.assertEqual(len(block.text), MAX_BLOCK_CHARS)

    def test_active_blocks_skip_disabled_and_empty(self):
        items = [
            Block(id="a", title="甲块", text="甲", enabled=True, sort=0),
            Block(id="b", title="乙块", text="乙", enabled=False, sort=1),
            Block(id="c", title="空块", text="   ", enabled=True, sort=2),
        ]
        parts = render_parts("", items)
        self.assertEqual([part["text"] for part in parts], ["甲"])
        parts = render_parts("基础", items)
        self.assertEqual([part["title"] for part in parts], ["基础文本", "甲块"])

    def test_compose_order(self):
        body = compose_body("基础", blocks("第一", "第二"))
        self.assertEqual(body, "基础\n\n第一\n\n第二")


class BlockStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "prompts.json"
        self.store = BlockStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        saved = self.store.save([{"title": "A", "text": "aaa"}, {"title": "B", "text": "bbb"}])
        self.assertEqual(len(saved), 2)
        loaded = self.store.load(force=True)
        self.assertEqual([item.title for item in loaded], ["A", "B"])
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 2)
        self.assertEqual(len(payload["blocks"]), 2)

    def test_missing_file(self):
        self.assertEqual(self.store.load(), [])

    def test_broken_file(self):
        self.path.write_text("{ not json", encoding="utf-8")
        self.assertEqual(self.store.load(force=True), [])

    def test_mtime_hot_reload(self):
        self.store.save([{"title": "A", "text": "aaa"}])
        self.assertEqual(len(self.store.load()), 1)
        self.path.write_text(
            json.dumps({"blocks": [{"title": "A", "text": "aaa"}, {"title": "B", "text": "bbb"}]}),
            encoding="utf-8",
        )
        import os
        import time

        os.utime(self.path, (time.time() + 5, time.time() + 5))
        self.assertEqual(len(self.store.load()), 2)


class RenderTest(unittest.TestCase):
    def test_block_shape(self):
        block = build_block(opts(), "", blocks("规则一"))
        self.assertTrue(block.startswith("<!-- savage-mode:L0:v"))
        self.assertIn("<rules>\n规则一\n</rules>", block)

    def test_no_wrap(self):
        block = build_block(opts(wrap_tag=""), "", blocks("规则一"))
        self.assertNotIn("<rules>", block)

    def test_empty_everything(self):
        self.assertEqual(build_block(opts(), "", []), "")
        self.assertIn("空的", preview_text(opts(), "", []))

    def test_fingerprint_tracks_content(self):
        first = build_block(opts(), "", blocks("甲"))
        second = build_block(opts(), "", blocks("乙"))
        self.assertNotEqual(first, second)

    def test_token_estimate(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("你好"), 2)
        self.assertGreater(estimate_tokens("hello world hello"), 2)

    def test_stats(self):
        stats = studio_stats("基础", blocks("第一"), opts())
        self.assertEqual(stats["block_count"], 2)
        self.assertEqual(stats["total_chars"], 2 + 2)
        self.assertGreater(stats["block_tokens"], stats["total_tokens"])
        self.assertEqual([part["source"] for part in stats["parts"]], ["config", "block"])


class InjectTest(unittest.TestCase):
    def test_prepend_and_append(self):
        block = build_block(opts(), "", blocks("规则"))
        prepended = inject("框架", block, "prepend")
        self.assertTrue(prepended.startswith("<!-- savage-mode"))
        self.assertTrue(prepended.endswith("框架"))
        appended = inject("框架", block, "append")
        self.assertTrue(appended.startswith("框架"))
        self.assertTrue(appended.endswith("</rules>"))

    def test_idempotent(self):
        block = build_block(opts(), "", blocks("规则"))
        once = inject("框架", block, "prepend")
        twice = inject(once, block, "prepend")
        self.assertEqual(once, twice)
        self.assertEqual(len(MARKER_RE.findall(twice)), 1)

    def test_old_body_replaced(self):
        old = build_block(opts(), "", blocks("旧规则"))
        with_old = inject("框架", old, "prepend")
        new = build_block(opts(), "", blocks("新规则"))
        updated = inject(with_old, new, "prepend", (old,))
        self.assertNotIn("旧规则", updated)
        self.assertIn("新规则", updated)

    def test_disable_cleans_up(self):
        block = build_block(opts(), "", blocks("规则"))
        with_block = inject("框架", block, "prepend")
        self.assertEqual(inject(with_block, "", "prepend", (block,)), "框架")

    def test_none_prompt(self):
        block = build_block(opts(), "", blocks("规则"))
        self.assertEqual(inject(None, block, "append"), block)

    def test_strip_previous_compacts(self):
        self.assertEqual(strip_previous("A\n\n<!-- savage-mode:L0:vabc -->\n\n\nB"), "A\n\nB")

    def test_prefix_fingerprint(self):
        block = build_block(opts(), "", blocks("规则"))
        prompt = inject("框架", block, "prepend")
        self.assertEqual(prefix_fingerprint(prompt, block), fingerprint("框架"))


class StateTest(unittest.TestCase):
    def test_cache_report(self):
        state = ModeState()
        block = build_block(opts(), "", blocks("规则"))
        for _ in range(3):
            state.remember(
                InjectionRecord(0.0, "umo", "webchat", fingerprint(block), fingerprint("框架"), 10),
                block,
            )
        stable, same, total = state.cache_report("umo")
        self.assertTrue(stable)
        self.assertEqual((same, total), (3, 3))
        self.assertEqual(state.last_umo, "umo")

    def test_cache_report_change(self):
        state = ModeState()
        block = build_block(opts(), "", blocks("规则"))
        state.remember(InjectionRecord(0.0, "umo", "webchat", "", fingerprint("A"), 10), block)
        state.remember(InjectionRecord(0.0, "umo", "webchat", "", fingerprint("B"), 10), block)
        stable, same, total = state.cache_report("umo")
        self.assertFalse(stable)
        self.assertEqual((same, total), (1, 2))

    def test_known_bodies(self):
        state = ModeState()
        block = build_block(opts(), "", blocks("规则"))
        state.remember(InjectionRecord(0.0, "umo", "webchat", "", "", 0), block)
        self.assertEqual(state.known_bodies("umo"), (block,))
        self.assertEqual(state.known_bodies("other"), ())


class StatusTest(unittest.TestCase):
    def test_status_lines(self):
        stats = studio_stats("", blocks("规则"), opts())
        lines = status_lines(opts(platforms=("webchat",)), "9.9.9", (True, 3, 3), 3, stats)
        text = "\n".join(lines)
        self.assertIn("Savage Mode · L0 v9.9.9", text)
        self.assertIn("开关 开", text)
        self.assertIn("稳定", text)
        self.assertIn("tokens", text)

    def test_block_lines(self):
        self.assertIn("块库为空", "\n".join(block_lines([])))
        lines = block_lines(blocks("规则"))
        self.assertIn("[开] 块0", "\n".join(lines))

    def test_preview_contains_content(self):
        self.assertIn("规则内容", preview_text(opts(), "", blocks("规则内容")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
