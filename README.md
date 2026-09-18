# Savage Mode

把**长期稳定**的系统提示词（角色设定、行为规则、输出格式、安全红线）注入 AstrBot 的
`ProviderRequest.system_prompt`。只做 L0 稳定层——不注入动态内容、不接管人格、不改上下文。

当前版本 `v0.2.1`，要求 AstrBot `>= 4.27,<5`。

## 一、它解决什么

AstrBot 的系统提示词由框架分层拼装（人格 → 技能 → 安全模式 → 沙箱/工具提示）。
插件能在 `on_llm_request` 里改它，但官方文档明确警告：

> `req.system_prompt += ...` 只适合追加**稳定、长期有效**的角色设定或全局规则；
> 把每轮变化的内容（当前时间、好感度、状态栏、短期记忆、检索摘要）写进去会破坏
> 模型服务端的提示词缓存，**成本约涨 7–20 倍**。

Savage Mode 把"稳定层"这件事做干净：**块库化管理、实时预览、token 计数、缓存自检、幂等注入**。

## 二、安装与使用

1. 插件目录放进 `data/plugins/`，重载插件，日志应出现 `Savage Mode (L0) loaded v0.2.1`。
2. WebUI → 插件 → **Savage Mode** → 打开面板（Plugin Page）：
   - **基础设置**：总开关 / 注入位置 / 生效平台 / 包裹标签 / 调试日志 / 基础文本；
   - **块库**：新增、编辑、启用停用、↑↓ 调序、删除，保存后立即生效；
   - **实时预览**：完整注入内容（含标记与包裹）、每个片段的字数与预估 tokens、
     上次请求 system prompt 长度与占比、缓存自检结果。
3. 指令（管理员）：

| 指令 | 说明 |
|---|---|
| `/savagemode status` | 开关/位置/平台/片段数/tokens/缓存自检 |
| `/savagemode preview` | 预览即将注入的完整内容 |
| `/savagemode blocks` | 列出块库与顺序 |

## 三、配置项（插件配置页）

| 配置项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| 总开关 | bool | 开 | 关闭后不注入，并清掉上一版残留 |
| 注入位置 | string | prepend | `prepend`=置于最前（规则优先）；`append`=追加末尾 |
| 基础文本 | text | 空 | 排在块库之前的单段文本，一般留空 |
| 生效平台 | list | 空 | 空=全部；填适配器名（`aiocqhttp`、`telegram`、`webchat`…） |
| 包裹标签 | string | rules | 用 `<rules>…</rules>` 包住正文；留空不包裹 |
| 调试日志 | bool | 关 | 每次注入/跳过写一条日志（含指纹与长度） |

## 四、块库数据

- 位置：`data/plugin_data/astrbot_plugin_savagemode/prompts.json`
- 结构：`{version, updated_at, blocks:[{id,title,text,enabled,sort}]}`，原子写入，可直接备份。
- 注入顺序：基础文本 → 启用的块（按 sort，面板里即上下顺序），整体只包一层标签。

## 五、缓存友好（核心设计）

1. **无模板变量**：不提供 `{{时间}}`、`{{用户名}}` 替换，从根上避免动态内容。
2. **字节级稳定**：同一份块库渲染出的内容永远一致，可被服务端缓存命中。
3. **幂等**：注入块带指纹标记 `<!-- savage-mode:L0:vXXXXXXXX -->`；重复注入只保留一份，
   改块库时旧正文会被清掉，不留残渣。
4. **自检**：面板与 `/savagemode status` 统计最近若干轮「去掉本块后的框架前缀」指纹，
   显示 `缓存自检 稳定 N/N`。位置为 `prepend` 时本块变更会让其后整段缓存失效，
   `append` 只影响尾部——按需取舍。

## 六、边界（不做什么）

- 不做会话级覆盖、不做动态注入（`extra_user_content_parts`）；
- 不修改 AstrBot 人格（`# Persona Instructions` 仍由框架注入，本插件只叠加）；
- 不改 `contexts`、不拦截/改写回复、不参与分段；
- 与"全接管型"插件（会覆写 `req.system_prompt` 的插件）同时启用时谁后执行谁生效，
  本插件不抢优先级，建议二选一。

## 七、测试

```powershell
$py = "C:\Users\24122\AppData\Local\Temp\opencode\itvenv\Scripts\python.exe"
& $py tests/test_core.py          # 纯逻辑，无依赖
& $py tests/test_integration.py   # 真实 ProviderRequest / 事件钩子 / 面板 API
```

实机验收步骤见 `SOAK.md`。
