/**
 * Savage Mode 面板测试：结构 + 真实交互（jsdom）。
 *
 * 用 stubbed AstrBotPluginPage 桥加载真实 pages/console/app.js，
 * 然后模拟：初始加载、加块、编辑、上下移、启用开关、保存、设置保存。
 *
 * 用法（需要 jsdom）：
 *   JSDOM_DIR=<...>/node_modules/.. node tests/test_panel.mjs
 *   # 或 NODE_PATH=<dir>/node_modules node tests/test_panel.mjs
 * 退出码 0 = 全过。
 */

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);

function loadJSDOM() {
  try {
    return require("jsdom").JSDOM;
  } catch (err) {
    const dir = process.env.JSDOM_DIR;
    if (!dir) {
      console.error("jsdom not found. Install it and set NODE_PATH or JSDOM_DIR.");
      throw err;
    }
    return require(join(dir, "node_modules", "jsdom")).JSDOM;
  }
}

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const PANEL = join(ROOT, "pages", "console");
const JSDOM = loadJSDOM();

function fixture(blocks) {
  const parts = blocks
    .filter((b) => b.enabled && b.text.trim())
    .map((b, i) => ({
      id: b.id || `b${i}`,
      title: b.title || "未命名块",
      text: b.text,
      source: "block",
      chars: b.text.length,
      tokens: b.text.length,
    }));
  const total = parts.reduce((sum, p) => sum + p.chars, 0);
  const body = parts.map((p) => p.text).join("\n\n");
  return {
    version: "0.2.0",
    settings: {
      enabled: true,
      position: "prepend",
      wrap_tag: "rules",
      platforms: [],
      base_text: "",
      debug_log: false,
    },
    blocks,
    preview: {
      body: body ? `<!-- savage-mode:L0:vabcd1234 -->\n<rules>\n${body}\n</rules>` : "",
      chars: body.length + 40,
      tokens: total,
    },
    stats: {
      parts,
      total_chars: total,
      total_tokens: total,
      block_chars: body.length + 40,
      block_tokens: total,
      block_count: parts.length,
    },
    cache: { stable: true, same: 3, total: 3, count: 3, last_total_len: 5000, last_tokens: 1500 },
    limits: { max_blocks: 50, max_block_chars: 20000, max_total_chars: 60000 },
    path: "data/plugin_data/astrbot_plugin_savagemode/prompts.json",
  };
}

const server = {
  blocks: [
    { id: "b1", title: "总则", text: "回答要简洁。", enabled: true, sort: 0 },
    { id: "b2", title: "红线", text: "不要编造。", enabled: true, sort: 1 },
  ],
  gets: [],
  posts: [],
};

async function apiGet(route) {
  server.gets.push(route);
  if (route === "state") return fixture(server.blocks.map((b, i) => ({ ...b, sort: i })));
  return {};
}

async function apiPost(route, body = {}) {
  server.posts.push({ route, body });
  if (route === "blocks/save") {
    server.blocks = (body.blocks || []).map((b, i) => ({ ...b, id: b.id || `new${i}`, sort: i }));
    return fixture(server.blocks);
  }
  if (route === "settings/save") {
    const values = body.values || {};
    const base = fixture(server.blocks.map((b, i) => ({ ...b, sort: i })));
    return { ...base, settings: { ...base.settings, ...values } };
  }
  if (route === "blocks/preview") {
    const blocks = body.blocks || [];
    const preview = fixture(blocks.map((b, i) => ({ ...b, sort: i })));
    return { preview: preview.preview, stats: preview.stats };
  }
  return { ok: true };
}

const html = readFileSync(join(PANEL, "index.html"), "utf8")
  .replace(
    /<link rel="stylesheet" href="style\.css"[^>]*>/,
    `<style>${readFileSync(join(PANEL, "style.css"), "utf8")}</style>`
  )
  .replace(/<script src="app\.js"><\/script>/, "");

const dom = new JSDOM(html, {
  runScripts: "dangerously",
  url: "http://localhost/",
  pretendToBeVisual: true,
});
const { window } = dom;
const { document } = window;

window.AstrBotPluginPage = { apiGet, apiPost, ready: async () => {} };
window.confirm = () => true;
window.eval(readFileSync(join(PANEL, "app.js"), "utf8"));

const $ = (id) => document.getElementById(id);
const tick = (ms = 25) => new Promise((resolve) => setTimeout(resolve, ms));
const plain = (value) => JSON.parse(JSON.stringify(value));
const click = (el) => {
  assert.ok(el, "click target must exist");
  el.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
};
const type = (el, value) => {
  el.value = value;
  el.dispatchEvent(new window.Event("input", { bubbles: true }));
};
const blockEls = () => [...$("blocks").querySelectorAll(".block")];

async function waitFor(predicate, label, timeout = 3000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (predicate()) return;
    await tick(20);
  }
  throw new Error(`timeout waiting for: ${label}`);
}

const results = [];
async function check(name, fn) {
  try {
    await fn();
    results.push([true, name]);
    console.log(`ok   ${name}`);
  } catch (err) {
    results.push([false, name]);
    console.log(`FAIL ${name}\n     ${err.message}`);
  }
}

// ---- 初始加载 ---------------------------------------------------------

await waitFor(() => blockEls().length === 2, "initial state load");

await check("徽章显示版本/开关/tokens/缓存自检", () => {
  assert.equal($("badge-version").textContent, "v0.2.0");
  assert.equal($("badge-switch").textContent, "注入中");
  assert.ok($("badge-tokens").textContent.includes("tokens"));
  assert.ok($("badge-cache").textContent.includes("稳定 3/3"));
});

await check("块按顺序渲染，编号补零、标题与字数正确", () => {
  const titles = blockEls().map((el) => el.querySelector(".title").value);
  assert.deepEqual(titles, ["总则", "红线"]);
  assert.equal(blockEls()[0].querySelector(".meta").textContent, "6 字");
  assert.equal(blockEls()[0].querySelector(".order").textContent, "01");
  assert.equal(blockEls()[1].querySelector(".order").textContent, "02");
});

await check("预览包含标记、包裹与块正文", () => {
  const text = $("preview").textContent;
  assert.ok(text.includes("<!-- savage-mode:L0:v"), "marker");
  assert.ok(text.includes("<rules>"), "wrap");
  assert.ok(text.includes("回答要简洁。"));
  assert.ok(text.includes("不要编造。"));
});

await check("分段明细表列出两个块并给出合计", () => {
  const rows = [...$("parts").querySelectorAll("tbody tr")];
  assert.equal(rows.length, 2);
  assert.ok($("parts").querySelector("tfoot").textContent.includes("合计"));
});

await check("基础设置回填自服务端", () => {
  assert.equal($("set-enabled").checked, true);
  assert.equal($("set-position").value, "prepend");
  assert.equal($("set-tag").value, "rules");
});

// ---- 交互 -------------------------------------------------------------

await check("新增块：出现第三行且提示未保存", () => {
  click($("btn-add"));
  assert.equal(blockEls().length, 3);
  assert.ok($("blocks-hint").textContent.includes("未保存"));
});

await check("编辑正文：字数与预览实时刷新", async () => {
  const textarea = blockEls()[2].querySelector("textarea");
  type(textarea, "第三条规则。");
  assert.equal(blockEls()[2].querySelector(".meta").textContent, "6 字");
  await waitFor(() => server.posts.some((p) => p.route === "blocks/preview"), "preview debounce");
  await waitFor(() => $("preview").textContent.includes("第三条规则。"), "preview update");
});

await check("上移调序：预览顺序跟着变", async () => {
  const up = blockEls()[1].querySelector('[data-act="up"]');
  click(up);
  await waitFor(() => blockEls()[0].querySelector(".title").value === "红线", "reorder");
  await waitFor(() => $("preview").textContent.indexOf("不要编造。") < $("preview").textContent.indexOf("回答要简洁。"), "preview order");
});

await check("停用开关：正文移出预览", async () => {
  const box = blockEls()[0].querySelector('[data-act="enabled"]');
  box.checked = false;
  box.dispatchEvent(new window.Event("change", { bubbles: true }));
  await waitFor(() => !$("preview").textContent.includes("不要编造。"), "disabled block removed");
});

await check("保存块库：POST 顺序与启用状态正确", async () => {
  await tick(400); // 等预览抖动
  click($("btn-save-blocks"));
  await waitFor(() => server.posts.some((p) => p.route === "blocks/save"), "save post");
  const saved = server.posts.find((p) => p.route === "blocks/save").body.blocks;
  assert.equal(saved.length, 3);
  assert.equal(saved[0].title, "红线");
  assert.equal(saved[0].enabled, false);
  assert.equal(saved[2].title, "规则 3");
  await waitFor(() => $("blocks-hint").textContent.includes("↑↓"), "dirty cleared");
});

await check("删除块：行数减少", () => {
  click(blockEls()[0].querySelector('[data-act="del"]'));
  assert.equal(blockEls().length, 2);
});

await check("保存设置：POST 规范化后的值", async () => {
  $("set-platforms").value = "telegram, webchat";
  $("set-tag").value = "";
  $("set-debug").checked = true;
  click($("btn-save-settings"));
  await waitFor(() => server.posts.some((p) => p.route === "settings/save"), "settings post");
  const values = server.posts.find((p) => p.route === "settings/save").body.values;
  assert.deepEqual(plain(values.platforms), ["telegram", "webchat"]);
  assert.equal(values.wrap_tag, "");
  assert.equal(values.debug_log, true);
});

await check("基础文本为空时不报错，路径展示", () => {
  assert.ok($("foot-path").textContent.includes("prompts.json"));
  assert.equal($("stat-ratio").textContent.endsWith("%") || $("stat-ratio").textContent === "—", true);
});

// ---- Editorial 风格漂移检查（对齐 stylekit 禁止项） ---------------------

const css = readFileSync(join(PANEL, "style.css"), "utf8");
const htmlSrc = readFileSync(join(PANEL, "index.html"), "utf8");

await check("禁止项：无阴影 / 无渐变 / 无背景图案", () => {
  assert.ok(!/box-shadow/.test(css), "box-shadow 出现");
  assert.ok(!/text-shadow/.test(css), "text-shadow 出现");
  assert.ok(!/gradient/i.test(css), "gradient 出现");
  assert.ok(!/url\(\s*['"]?data:image\/svg/.test(css), "装饰性 SVG 背景出现");
});

await check("禁止项：无圆角（只允许 0）", () => {
  const radii = [...css.matchAll(/border-radius:\s*([^;]+);/g)].map((m) => m[1].trim());
  assert.ok(radii.length > 0, "应显式声明 border-radius: 0");
  for (const value of radii) {
    assert.ok(/^0(px)?$/.test(value), `出现非零圆角: ${value}`);
  }
});

await check("禁止项：无彩色强调色（纯单色体系）", () => {
  const hexes = [...css.matchAll(/#[0-9a-fA-F]{3,8}/g)].map((m) => m[0].toLowerCase());
  const allowed = new Set(["#f9f8f6", "#1c1c1c"]);
  for (const hex of hexes) {
    assert.ok(allowed.has(hex), `出现非规范色值: ${hex}`);
  }
  assert.ok(!/rgb\(/.test(css), "出现 rgb() 彩色");
});

await check("必须项：暖米底 / 柔和黑 / 透明度层次 / 衬线标题", () => {
  assert.ok(css.includes("#F9F8F6"), "缺少 #F9F8F6 背景");
  assert.ok(css.includes("#1C1C1C"), "缺少 #1C1C1C 文字");
  assert.ok(/--ink-60/.test(css) && /--ink-40/.test(css) && /--line\b/.test(css), "缺少透明度层次 token");
  assert.ok(/font-family:\s*var\(--serif\)/.test(css), "标题未使用衬线变量");
  assert.ok(/letter-spacing:\s*0\.2em/.test(css) && /text-transform:\s*uppercase/.test(css), "缺少 uppercase tracking 标签");
  assert.ok(/prefers-reduced-motion/.test(css), "缺少 reduced-motion 备选");
  assert.ok(/hover:?.*after|::after/.test(css) && /scaleX/.test(css), "缺少 hover-underline 动画");
  assert.ok(/hero-title/.test(htmlSrc) && !/style=/.test(htmlSrc.replace(/style="[^"]*"/g, (m) => m.includes("padding") ? "" : m)), "hero 结构异常");
});

const failed = results.filter(([ok]) => !ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
