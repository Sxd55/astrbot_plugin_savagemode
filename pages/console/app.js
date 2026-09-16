/* Savage Mode · L0 面板
   风格：Editorial（编辑杂志风）。桥：window.AstrBotPluginPage（apiGet/apiPost）。
   交互约定：无圆角、无阴影、纯单色；hover 只做排版型反馈（下划线延展 / 标题斜体 / 分隔线加深）。 */

const $ = (id) => document.getElementById(id);
let STATE = null;
let BLOCKS = [];
let DIRTY = false;
let previewTimer = null;

const HINT_IDLE = "按顺序拼接注入 · 用 ↑↓ 调序";
const HINT_DIRTY = "有未保存的修改，记得点「保存块库」";

function unwrap(result) {
  if (result && typeof result === "object" && "status" in result && "data" in result) {
    if (result.status === "error") {
      const err = new Error(result.message || "request failed");
      err.payload = result;
      throw err;
    }
    return result.data;
  }
  return result;
}

async function apiGet(route, query = {}) {
  const bridge = window.AstrBotPluginPage;
  if (!bridge?.apiGet) throw new Error("AstrBotPluginPage 未就绪，请在 AstrBot 拓展页打开");
  return unwrap(await bridge.apiGet(route, query));
}

async function apiPost(route, body = {}) {
  const bridge = window.AstrBotPluginPage;
  if (!bridge?.apiPost) throw new Error("AstrBotPluginPage 未就绪，请在 AstrBot 拓展页打开");
  return unwrap(await bridge.apiPost(route, body));
}

function toast(msg) {
  const box = $("toast");
  box.hidden = false;
  box.textContent = msg;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { box.hidden = true; }, 2200);
}

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function parsePlatforms(value) {
  return String(value || "")
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

// ---- 渲染 -----------------------------------------------------------

function renderBadges() {
  const settings = STATE.settings;
  const cache = STATE.cache;
  $("badge-version").textContent = `v${STATE.version}`;
  $("badge-switch").textContent = settings.enabled ? "注入中" : "已关闭";
  $("badge-switch").classList.toggle("off", !settings.enabled);
  $("badge-tokens").textContent = `约 ${STATE.stats.block_tokens} tokens`;
  const stable = cache.stable && cache.total >= 2;
  $("badge-cache").textContent = cache.total >= 2
    ? (stable ? `缓存自检 稳定 ${cache.same}/${cache.total}` : `缓存自检 有变化 ${cache.same}/${cache.total}`)
    : "缓存自检 样本不足";
  $("badge-cache").classList.toggle("warn", !stable && cache.total >= 2);
}

function renderSettings() {
  const settings = STATE.settings;
  $("set-enabled").checked = !!settings.enabled;
  $("set-position").value = settings.position === "append" ? "append" : "prepend";
  $("set-platforms").value = (settings.platforms || []).join(", ");
  $("set-tag").value = settings.wrap_tag || "";
  $("set-debug").checked = !!settings.debug_log;
  $("set-base").value = settings.base_text || "";
}

function renderBlocks() {
  const host = $("blocks");
  if (!BLOCKS.length) {
    host.innerHTML = `<p class="italic-quiet" style="padding: 2rem 0;">还没有块。点「+ 新增块」写第一条规则。</p>`;
    return;
  }
  host.innerHTML = BLOCKS.map((block, index) => `
    <article class="entry block ${block.enabled ? "" : "off"}" data-index="${index}">
      <div class="order">${String(index + 1).padStart(2, "0")}</div>
      <div class="entry-main">
        <div class="entry-head">
          <input class="entry-title-input title" data-act="title" value="${esc(block.title)}"
                 placeholder="块名（只是给你看的）" aria-label="块名" />
          <span class="label entry-meta meta">${block.text.length} 字</span>
          <label class="check-line" title="启用 / 停用">
            <input type="checkbox" data-act="enabled" ${block.enabled ? "checked" : ""} aria-label="启用该块" />
            <span class="label">启用</span>
          </label>
          <button class="link-btn label" data-act="up" aria-label="上移" title="上移" ${index === 0 ? "disabled" : ""}>↑</button>
          <button class="link-btn label" data-act="down" aria-label="下移" title="下移" ${index === BLOCKS.length - 1 ? "disabled" : ""}>↓</button>
          <button class="link-btn label" data-act="del" aria-label="删除" title="删除">删除</button>
        </div>
        <textarea class="entry-text" data-act="text" rows="3"
          placeholder="写稳定的规则 / 设定；不要写时间、用户名等每轮变化的内容">${esc(block.text)}</textarea>
      </div>
    </article>
  `).join("");
}

function renderPreview() {
  const preview = STATE.preview || {};
  $("preview").textContent = preview.body || "（空：不会注入任何内容）";
  $("stat-chars").textContent = preview.chars ?? 0;
  $("stat-tokens").textContent = preview.tokens ?? 0;
  const last = STATE.cache?.last_total_len || 0;
  $("stat-last").textContent = last ? `${last} 字 / 约 ${STATE.cache.last_tokens} tokens` : "—";
  $("stat-ratio").textContent = last ? `${Math.round(((preview.chars || 0) / last) * 100)}%` : "—";
  $("preview-meta").textContent = `位置 ${STATE.settings.position === "append" ? "追加" : "前置"}`
    + ` · ${STATE.stats.block_count} 个片段 · 共 ${STATE.stats.total_chars} 字`;

  const rows = (STATE.stats.parts || []).map((part) => `
    <tr>
      <td>${esc(part.title)}</td>
      <td class="num">${part.chars}</td>
      <td class="num">${part.tokens}</td>
      <td class="dim">${part.source === "config" ? "基础文本" : "块库"}</td>
    </tr>
  `).join("");
  $("parts").innerHTML = `
    <thead><tr><th>片段</th><th class="num">字数</th><th class="num">tokens</th><th>来源</th></tr></thead>
    <tbody>${rows || `<tr><td colspan="4" class="dim">（无）</td></tr>`}</tbody>
    <tfoot><tr><th>合计</th><th class="num">${STATE.stats.total_chars}</th><th class="num">${STATE.stats.total_tokens}</th><th></th></tr></tfoot>
  `;
}

function renderAll() {
  renderBadges();
  renderSettings();
  renderBlocks();
  renderPreview();
  $("blocks-hint").textContent = HINT_IDLE;
  $("blocks-hint").classList.remove("warn");
  $("foot-path").textContent = `数据文件 · ${STATE.path}`;
}

function applyState(next) {
  STATE = next;
  BLOCKS = (next.blocks || []).map((block) => ({ ...block }));
  DIRTY = false;
  renderAll();
}

// ---- 交互 -----------------------------------------------------------

async function loadState() {
  applyState(await apiGet("state"));
}

async function saveSettings() {
  const values = {
    enabled: $("set-enabled").checked,
    position: $("set-position").value,
    platforms: parsePlatforms($("set-platforms").value),
    wrap_tag: $("set-tag").value.trim(),
    debug_log: $("set-debug").checked,
    l0_text: $("set-base").value,
  };
  applyState(await apiPost("settings/save", { values }));
  toast("设置已保存");
}

async function saveBlocks() {
  applyState(await apiPost("blocks/save", { blocks: BLOCKS }));
  toast("块库已保存");
}

async function refreshPreview() {
  const payload = BLOCKS.map((block, index) => ({ ...block, sort: index }));
  const data = await apiPost("blocks/preview", { blocks: payload });
  STATE.preview = data.preview;
  STATE.stats = data.stats;
  renderPreview();
  renderBadges();
}

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(() => {
    refreshPreview().catch(() => {});
  }, 350);
}

function markDirty() {
  if (!DIRTY) {
    DIRTY = true;
    $("blocks-hint").textContent = HINT_DIRTY;
    $("blocks-hint").classList.add("warn");
  }
}

function reorder(index, delta) {
  const target = index + delta;
  if (target < 0 || target >= BLOCKS.length) return;
  const [item] = BLOCKS.splice(index, 1);
  BLOCKS.splice(target, 0, item);
  markDirty();
  renderBlocks();
  schedulePreview();
}

function bindBlocks() {
  $("blocks").addEventListener("input", (event) => {
    const host = event.target.closest(".block");
    if (!host) return;
    const index = Number(host.dataset.index);
    const act = event.target.dataset.act;
    if (act === "title") BLOCKS[index].title = event.target.value;
    else if (act === "text") BLOCKS[index].text = event.target.value;
    markDirty();
    if (act === "text") {
      host.querySelector(".meta").textContent = `${event.target.value.length} 字`;
      schedulePreview();
    }
  });
  $("blocks").addEventListener("change", (event) => {
    const host = event.target.closest(".block");
    if (!host) return;
    if (event.target.dataset.act === "enabled") {
      BLOCKS[Number(host.dataset.index)].enabled = event.target.checked;
      markDirty();
      schedulePreview();
    }
  });
  $("blocks").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-act]");
    if (!button) return;
    const host = button.closest(".block");
    const index = Number(host.dataset.index);
    const act = button.dataset.act;
    if (act === "up") reorder(index, -1);
    else if (act === "down") reorder(index, 1);
    else if (act === "del") {
      if (!confirm(`删除「${BLOCKS[index].title || "未命名块"}」？保存后生效。`)) return;
      BLOCKS.splice(index, 1);
      markDirty();
      renderBlocks();
      schedulePreview();
    }
  });
}

function bindAll() {
  $("btn-reload").addEventListener("click", () => loadState().then(() => toast("已刷新")).catch((err) => toast(err.message)));
  $("btn-save-settings").addEventListener("click", () => saveSettings().catch((err) => toast(err.message)));
  $("btn-save-blocks").addEventListener("click", () => saveBlocks().catch((err) => toast(err.message)));
  $("btn-add").addEventListener("click", () => {
    BLOCKS.push({ id: "", title: `规则 ${BLOCKS.length + 1}`, text: "", enabled: true, sort: BLOCKS.length });
    markDirty();
    renderBlocks();
    const host = $("blocks").querySelector(".block:last-child textarea");
    host?.focus();
  });
  bindBlocks();
}

window.addEventListener("beforeunload", (event) => {
  if (!DIRTY) return;
  event.preventDefault();
  event.returnValue = "";
});

bindAll();
loadState().catch((err) => toast(err.message));
