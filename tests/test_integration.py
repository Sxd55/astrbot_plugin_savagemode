"""集成测试：对着真实 astrbot 包驱动钩子、指令与面板 API。

    <venv>/Scripts/python tests/test_integration.py -v

没装 astrbot 时整个模块跳过；test_core.py 保持零依赖。
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# import astrbot 会在 CWD 建 ./data，切到临时目录保持仓库干净。
import os  # noqa: E402

_IT_WORKDIR = os.path.join(tempfile.gettempdir(), "savage-mode-it")
os.makedirs(_IT_WORKDIR, exist_ok=True)
os.chdir(_IT_WORKDIR)

try:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.message_components import Plain
    from astrbot.api.provider import ProviderRequest
    from astrbot.core.platform.astrbot_message import (
        AstrBotMessage,
        MessageMember,
        MessageType,
    )
    from astrbot.core.platform.platform_metadata import PlatformMetadata

    import main as plugin_main

    HAS_ASTRBOT = True
except Exception:  # noqa: BLE001
    HAS_ASTRBOT = False


class FakeContext:
    def __init__(self):
        self.routes = {}

    def register_web_api(self, route, handler, methods, *args, **kwargs):
        self.routes[route] = (handler, methods)


class FakeRequest:
    def __init__(self, body=None):
        self._body = body or {}

    async def json(self, default=None):
        return self._body if self._body is not None else (default or {})


def make_event(text="你好", platform="aiocqhttp", umo="webchat:u1"):
    msg = AstrBotMessage()
    msg.type = MessageType.FRIEND_MESSAGE
    msg.self_id = "bot1"
    msg.sender = MessageMember(user_id="u1", nickname="阿U")
    msg.message = [Plain(text)]
    msg.message_str = text
    meta = PlatformMetadata(name=platform, description="test", id=platform)
    return AstrMessageEvent(text, msg, meta, umo)


def _json(resp):
    return json.loads(resp.body.decode("utf-8"))


@unittest.skipUnless(HAS_ASTRBOT, "astrbot package not installed")
class SavageModeIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = FakeContext()
        self.plugin = plugin_main.SavageModePlugin(self.ctx, self._config())
        # 沙箱化存储路径
        self.plugin.store.path = pathlib.Path(self.tmp.name) / "prompts.json"
        plugin_main.request = FakeRequest()

    def tearDown(self):
        self.tmp.cleanup()

    def _config(self, **overrides):
        config = {
            "enabled": True,
            "position": "prepend",
            "l0_text": "",
            "platforms": [],
            "wrap_tag": "rules",
            "debug_log": False,
        }
        config.update(overrides)
        return config

    def _seed(self, *texts):
        self.plugin.store.save([{"title": f"块{i}", "text": text} for i, text in enumerate(texts)])

    def _run(self, req, event=None):
        event = event or make_event()
        asyncio.run(self.plugin.on_llm_request(event, req))
        return req.system_prompt or ""

    # -- 加载 / 注册 ---------------------------------------------------

    def test_pages_registered(self):
        self.assertIn("/astrbot_plugin_savagemode/state", self.ctx.routes)
        self.assertIn("/astrbot_plugin_savagemode/blocks/save", self.ctx.routes)
        self.assertIn("/astrbot_plugin_savagemode/settings/save", self.ctx.routes)

    # -- 注入 ----------------------------------------------------------

    def test_inject_blocks(self):
        self._seed("规则一", "规则二")
        req = ProviderRequest(prompt="嗨", session_id="s")
        req.system_prompt = "框架人格"
        out = self._run(req)
        self.assertTrue(out.startswith("<!-- savage-mode:L0:v"))
        self.assertIn("规则一\n\n规则二", out)
        self.assertTrue(out.rstrip().endswith("框架人格"))

    def test_base_text_before_blocks(self):
        self.plugin.config = self._config(l0_text="基础文本")
        self._seed("规则一")
        out = self._run(ProviderRequest(prompt="嗨", session_id="s"))
        self.assertIn("基础文本\n\n规则一", out)

    def test_disabled_blocks_not_injected(self):
        self._seed("开着的")
        self.plugin.store.save(
            [
                {"title": "开", "text": "开着的", "enabled": True},
                {"title": "关", "text": "关掉的", "enabled": False},
            ]
        )
        out = self._run(ProviderRequest(prompt="嗨", session_id="s"))
        self.assertIn("开着的", out)
        self.assertNotIn("关掉的", out)

    def test_idempotent_across_turns(self):
        self._seed("规则")
        first = self._run(ProviderRequest(prompt="a", session_id="s"), make_event(umo="u"))
        req = ProviderRequest(prompt="b", session_id="s")
        req.system_prompt = first
        second = self._run(req, make_event(umo="u"))
        self.assertEqual(first, second)

    def test_platform_filter(self):
        self._seed("规则")
        self.plugin.config = self._config(platforms=["telegram"])
        req = ProviderRequest(prompt="嗨", session_id="s")
        req.system_prompt = "框架人格"
        self.assertEqual(self._run(req, make_event(platform="aiocqhttp")), "框架人格")
        out = self._run(ProviderRequest(prompt="嗨", session_id="s"), make_event(platform="telegram"))
        self.assertIn("规则", out)

    def test_disable_cleans_previous(self):
        self._seed("规则")
        req = ProviderRequest(prompt="a", session_id="s")
        with_block = self._run(req, make_event(umo="u"))
        self.plugin.config = self._config(enabled=False)
        req2 = ProviderRequest(prompt="b", session_id="s")
        req2.system_prompt = with_block
        self.assertEqual(self._run(req2, make_event(umo="u")), "")

    def test_broken_event_still_injects(self):
        class Broken:
            def get_platform_name(self):
                raise RuntimeError("boom")

            @property
            def unified_msg_origin(self):
                raise RuntimeError("boom")

        self._seed("规则")
        req = ProviderRequest(prompt="嗨", session_id="s")
        req.system_prompt = "框架人格"
        asyncio.run(self.plugin.on_llm_request(Broken(), req))
        self.assertIn("规则", req.system_prompt or "")

    # -- 面板 API ------------------------------------------------------

    def test_state_api(self):
        self._seed("规则一")
        payload = _json(asyncio.run(self.plugin.page_state()))
        self.assertEqual(payload["version"], plugin_main.__version__)
        self.assertEqual(len(payload["blocks"]), 1)
        self.assertTrue(payload["preview"]["body"].startswith("<!-- savage-mode:L0:v"))
        self.assertGreater(payload["preview"]["tokens"], 0)
        self.assertIn("max_blocks", payload["limits"])
        self.assertEqual(payload["stats"]["block_count"], 1)

    def test_blocks_save_roundtrip(self):
        body = {
            "blocks": [
                {"id": "", "title": "A", "text": "甲", "enabled": True, "sort": 1},
                {"id": "bb", "title": "B", "text": "乙", "enabled": True, "sort": 0},
            ]
        }
        plugin_main.request = FakeRequest(body)
        payload = _json(asyncio.run(self.plugin.page_blocks_save()))
        self.assertEqual([item["title"] for item in payload["blocks"]], ["B", "A"])
        self.assertEqual([item["sort"] for item in payload["blocks"]], [0, 1])
        self.assertIn("乙\n\n甲", payload["preview"]["body"])

    def test_blocks_save_rejects_bad_payload(self):
        plugin_main.request = FakeRequest({"blocks": "nope"})
        resp = asyncio.run(self.plugin.page_blocks_save())
        self.assertEqual(resp.status_code, 400)

    def test_blocks_preview_unsaved(self):
        plugin_main.request = FakeRequest({"blocks": [{"title": "临时", "text": "未保存的"}]})
        payload = _json(asyncio.run(self.plugin.page_blocks_preview()))
        self.assertIn("未保存的", payload["preview"]["body"])
        # 不应落盘
        self.assertEqual(self.plugin.store.load(force=True), [])

    def test_settings_save(self):
        plugin_main.request = FakeRequest(
            {
                "values": {
                    "enabled": False,
                    "position": "append",
                    "wrap_tag": "<mode>",
                    "platforms": "telegram, webchat",
                    "debug_log": True,
                    "l0_text": "基础",
                }
            }
        )
        payload = _json(asyncio.run(self.plugin.page_settings_save()))
        settings = payload["settings"]
        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["position"], "append")
        self.assertEqual(settings["wrap_tag"], "mode")
        self.assertEqual(settings["platforms"], ["telegram", "webchat"])
        self.assertTrue(settings["debug_log"])
        self.assertEqual(settings["base_text"], "基础")

    def test_settings_save_rejects_bad_payload(self):
        plugin_main.request = FakeRequest({"values": "nope"})
        resp = asyncio.run(self.plugin.page_settings_save())
        self.assertEqual(resp.status_code, 400)

    # -- 指令 ----------------------------------------------------------

    def test_commands(self):
        class CmdEvent:
            def plain_result(self, text):
                return text

            def get_platform_name(self):
                return "webchat"

            @property
            def unified_msg_origin(self):
                return "webchat:u1"

        async def drain(agen):
            return [item async for item in agen]

        self._seed("规则一")
        event = CmdEvent()
        status = asyncio.run(drain(self.plugin.cmd_status(event)))
        preview = asyncio.run(drain(self.plugin.cmd_preview(event)))
        blocks = asyncio.run(drain(self.plugin.cmd_blocks(event)))
        self.assertIn("Savage Mode · L0 v", status[0])
        self.assertIn("规则一", preview[0])
        self.assertIn("块0", blocks[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
