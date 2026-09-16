# CLAUDE.md —— astrbot_plugin_savagemode（Savage Mode · L0）

## 项目定位

AstrBot 插件：把**长期稳定**的系统提示词（角色设定、行为规则、输出格式、安全红线）
注入 `ProviderRequest.system_prompt`，并提供可视块库（增删改查/排序/实时预览/token 计数）。

只做 L0 层。**不做**：动态内容（时间/好感度/记忆/检索）、会话级覆盖（L1）、
`extra_user_content_parts`（L2）、接管人格、改写 contexts、拦截回复、分段。

（前身：`astrbot_plugin_prompt_studio` v0.1.0，M1 单文本版；v0.2.0 改名 Savage Mode 并加面板。）

## 目录约定

```
main.py                # 插件类：钩子 + 指令 + 面板 API（薄，逻辑都在包里）
savagemode/            # 纯逻辑包，禁止 import astrbot（保证可单测）
  __init__.py          # PLUGIN_NAME / __version__
  l0.py                # 选项/块模型/渲染/幂等注入/指纹/统计/状态行
  store.py             # prompts.json 原子读写 + mtime 热加载
pages/console/         # 面板（index.html + app.js + style.css + logo.png）
tests/
  test_core.py         # 不依赖 astrbot
  test_integration.py  # 真 ProviderRequest / 真事件 / 面板 API；跑前 chdir 临时目录
metadata.yaml          # display_name: Savage Mode；logo.png 为插件图标
README.md / SOAK.md    # 用户文档 / 实机验收
_conf_schema.json      # 配置（编辑走 AstrBot 原生配置页；块库走面板）
```

## 硬规则

1. **缓存友好**：注入内容必须字节级稳定。禁止把时间、用户名、会话 ID、随机数
   等每轮变化的内容写进 `system_prompt`；本插件也不提供模板变量。
2. **幂等**：重复注入只保留一份（`<!-- savage-mode:L0:v<fp> -->` 标记 + 旧正文清理）。
3. **不阻断**：钩子/面板里任何异常只记日志或返回错误 JSON，绝不能让 LLM 请求失败。
4. **不抢戏**：不覆盖 AstrBot 人格，只做叠加。
5. **数据在 data/**：块库写 `data/plugin_data/astrbot_plugin_savagemode/prompts.json`（原子写）。
6. **版本号三处一致**：`metadata.yaml` / `savagemode/__init__.py` / `README.md`。

## 验证

```powershell
$py = "C:\Users\24122\AppData\Local\Temp\opencode\itvenv\Scripts\python.exe"
& $py tests/test_core.py
& $py tests/test_integration.py
```

面板改动后至少用手动方式过一遍：加载面板 → 加块 → 保存 → 真机发消息 → 查注入日志。
