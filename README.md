# QQ Music MCP

一个在本机运行的 QQ 音乐个人音乐库 MCP Server。AI 客户端可以读取和管理歌单、歌曲和分类计划；“我喜欢”整理只是其中一个高层工作流。

> 仅支持 Windows 10/11。QQ 音乐没有提供这套功能的官方开放 API，本项目使用网站当前的非官方接口，可能随上游改版而失效。

## 你可以让 AI 做什么

- 列出和读取全部自建歌单，也可以只读读取“我喜欢”。
- 创建歌单、批量加歌、移歌和删除空歌单。
- 完整导出“我喜欢”为 JSON、CSV 和摘要备份。
- 分析整个音乐库：重复歌曲、同歌名不同版本、空歌单、歌单交集、未整理的“我喜欢”。
- 按歌手、专辑、关键词和时长筛选歌曲，创建规则歌单。
- 合并歌单、拆分歌单、复制歌单，同时保留原始歌单。
- 搜索歌曲、读取歌曲详情和歌词。
- 让 AI 按语言、流派、年代、场景或歌手整理/分类/新建歌单。
- 一首歌可进入最多 3 张歌单；写入前先预览，冻结计划后才写回。
- 自动创建或精确复用唯一同名歌单，分批写入并逐批回读验证。
- 记录每次运行和通用操作，支持整理计划的有限回滚。

## 两层能力

### 通用音乐库操作

这些工具直接对应安全封装后的 QQ 音乐歌单能力：

```text
qqmusic_list_playlists
qqmusic_get_playlist
qqmusic_create_playlist
qqmusic_add_songs
qqmusic_remove_songs
qqmusic_delete_playlist
```

音乐库分析工具：

```text
qqmusic_analyze_library
qqmusic_find_duplicates
qqmusic_find_empty_playlists
qqmusic_find_unorganized_songs
qqmusic_compare_playlists
```

智能歌单工具：

```text
qqmusic_create_smart_playlist
qqmusic_merge_playlists
qqmusic_split_playlist
```

搜索与音乐信息工具：

```text
qqmusic_search
qqmusic_get_song_detail
qqmusic_get_lyrics
```

### 高层 AI 工作流

现有的导出、计划、预览、冻结、探针、应用和回滚工具组成“整理我喜欢”工作流。它只是建立在上面通用能力之上的一个安全自动化流程。

## 使用场景

### 1. 先做一次音乐库体检

```text
请分析我的 QQ 音乐音乐库，不要修改任何内容。
告诉我有多少空歌单、重复歌曲、歌单之间的交集，
以及“我喜欢”里还没有进入任何其他歌单的歌曲。
```

AI 会调用 `qqmusic_analyze_library`，必要时继续调用
`qqmusic_find_duplicates`、`qqmusic_find_empty_playlists`、
`qqmusic_find_unorganized_songs` 和 `qqmusic_compare_playlists`。

### 2. 合并歌单但保留原歌单

```text
把“通勤”和“开车”合并成一张“驾驶精选”。
重复歌曲只保留一份，原来的两张歌单不要修改。
```

AI 会读取两张源歌单，调用 `qqmusic_merge_playlists`。新歌单写入后会回读确认，源歌单不会被删除。

### 3. 把一张大歌单拆成多个场景

```text
读取我的“收藏精选”，按歌曲气质拆成“通勤”“运动”“夜晚”三张歌单。
先展示每张歌单包含哪些歌，确认后再创建；原歌单保留。
```

AI 会先调用 `qqmusic_get_playlist`，根据歌曲元数据生成分组预览，再调用 `qqmusic_split_playlist`。

### 4. 用规则持续创建歌单

```text
从“我喜欢”里找出周杰伦的歌，创建一张“周杰伦精选”，最多 100 首，
不要重复，也不要动“我喜欢”。
```

AI 会使用 `qqmusic_create_smart_playlist`，规则可以组合歌手、专辑、关键词和时长：

```json
{
  "keyword": "",
  "singer": "周杰伦",
  "album": "",
  "min_duration_seconds": null,
  "max_duration_seconds": null,
  "limit": 100,
  "deduplicate": true
}
```

`source_directory_id=201` 表示从“我喜欢”读取；它只作为来源，不会成为写入目标。

### 5. 搜索歌曲、查详情和歌词

```text
搜索“晴天”，告诉我有哪些版本；再读取最匹配版本的歌曲详情和歌词。
```

AI 会调用 `qqmusic_search`，再根据返回的 MID 调用
`qqmusic_get_song_detail` 和 `qqmusic_get_lyrics`。

### 6. 整理“我喜欢”

```text
请整理我的 QQ 音乐“我喜欢”：先备份，按语言、流派和使用场景分类，
低置信度的歌放入“待整理”。先给我预览和分类理由，确认后再写入。
绝不从“我喜欢”删除歌曲。
```

这是现有的高层计划工作流，工具顺序见下方“推荐使用流程”。

## 推荐使用流程

整理“我喜欢”时，建议让 AI 严格按下面的顺序执行：

1. 调用 `qqmusic_status`，确认登录状态和本地能力可用。
2. 调用 `qqmusic_export_liked`，完整备份“我喜欢”，取得 `export_id`。
3. 调用 `qqmusic_get_export_summary` 查看整体信息，再用 `qqmusic_get_export_page` 分页读取歌曲，避免一次载入过多内容。
4. 调用 `qqmusic_create_plan` 创建草稿，并用 `qqmusic_set_taxonomy` 定义分类。系统会自动保留“待整理”分类。
5. AI 分析歌曲后，分批调用 `qqmusic_upsert_assignments` 写入分类、置信度和理由；每首歌最多进入 3 个分类。
6. 调用 `qqmusic_preview_plan` 检查覆盖率、各歌单数量和待整理歌曲。此时不会修改 QQ 音乐。
7. 需要调整时继续修改草稿；确认无误后调用 `qqmusic_finalize_plan` 冻结方案并生成 SHA-256。冻结后不能直接修改，可用 `qqmusic_revise_plan` 创建新修订。
8. 调用 `qqmusic_probe_write`，用临时歌单验证当前 QQ 音乐接口是否支持创建、加歌、回读、移除和删除。
9. 只有预览已确认、方案已冻结且探针成功后，才调用 `qqmusic_apply_plan` 创建或复用目标歌单并分批加歌。
10. 如果本次整理结果需要撤销，使用应用结果中的 `run_id` 调用 `qqmusic_rollback_run`。它只撤销该次运行记录的新增歌曲，不会改动“我喜欢”。

可以直接对 AI 这样说：

```text
请按推荐流程整理我的“我喜欢”。先检查状态并完整备份，然后分页读取歌曲，
按语言、流派和使用场景建立分类；低置信度歌曲放入“待整理”。
完成后只给我预览，不要写入。等我明确确认后，再冻结方案、运行写入探针并应用。
任何时候都不要从“我喜欢”删除歌曲。
```

## 环境要求

- Windows 10 或 Windows 11
- Python 3.11 或更高版本
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- 已安装 Chrome 或 Edge
- 支持 MCP 的 AI 客户端

## 安装

从 GitHub 安装为独立命令：

```powershell
uv tool install "git+https://github.com/baoozak/qqmusic-mcp.git"
qqmusic-mcp --help
```

仓库发布到 PyPI 后可直接使用：

```powershell
uv tool install qqmusic-mcp
```

发行包名和命令名统一为 `qqmusic-mcp`。

升级或卸载：

```powershell
uv tool upgrade qqmusic-mcp
uv tool uninstall qqmusic-mcp
```

## 接入 MCP 客户端

推荐使用标准 `stdio` 传输。客户端负责启动和关闭 MCP，不需要端口、后台服务或 Bearer Token。

### Codex

自动注册：

```powershell
qqmusic-mcp install --client codex
codex mcp get qqmusic-mcp
```

等价的手动命令：

```powershell
codex mcp add qqmusic-mcp -- qqmusic-mcp stdio
```

### Claude Desktop

生成配置：

```powershell
qqmusic-mcp config --client claude
```

将输出中的 `mcpServers` 合并到 Claude Desktop 配置文件。典型配置如下：

```json
{
  "mcpServers": {
    "qqmusic-mcp": {
      "command": "qqmusic-mcp",
      "args": ["stdio"]
    }
  }
}
```

### Cursor

```powershell
qqmusic-mcp config --client cursor
```

将输出合并到项目 `.cursor/mcp.json` 或 Cursor 的全局 MCP 配置。

### VS Code

```powershell
qqmusic-mcp config --client vscode
```

将输出保存或合并到 `.vscode/mcp.json`：

```json
{
  "servers": {
    "qqmusic-mcp": {
      "type": "stdio",
      "command": "qqmusic-mcp",
      "args": ["stdio"]
    }
  }
}
```

如果客户端找不到 `qqmusic-mcp`，运行 `Get-Command qqmusic-mcp`，然后把配置中的 `command` 换成输出的完整路径。

## 首次登录

AI 客户端第一次调用 MCP 时，会打开一个隔离的 Chrome 窗口；Chrome 不可用时尝试 Edge。使用 QQ 或微信登录 QQ 音乐即可。检测到登录态后窗口自动关闭。

登录 Cookie 不会以明文写入磁盘、日志或 MCP 响应。它通过 Windows DPAPI 加密保存到：

```text
%LOCALAPPDATA%\QQMusicOrganizer\session.dpapi
```

该文件只能由当前 Windows 用户解密。后续启动会自动复用；只有 QQ 音乐明确返回登录失效时才重新打开登录窗口。主动退出：

```powershell
qqmusic-mcp logout
```

## HTTP 模式

只有确实需要固定本地 URL 时才使用 Streamable HTTP。服务只绑定 `127.0.0.1`，并强制要求至少 32 字符的 Bearer Token。

```powershell
$token = qqmusic-mcp token
[Environment]::SetEnvironmentVariable("QQMUSIC_ORGANIZER_TOKEN", $token, "User")
$env:QQMUSIC_ORGANIZER_TOKEN = $token
qqmusic-mcp start
qqmusic-mcp status
```

MCP URL：`http://127.0.0.1:8765/mcp`

停止服务：

```powershell
qqmusic-mcp stop
```

直接前台运行也可以：

```powershell
qqmusic-mcp serve --port 8765 --login-timeout 600
```

## CLI

```text
qqmusic-mcp stdio                     标准 MCP stdio 服务
qqmusic-mcp serve                     前台 HTTP 服务
qqmusic-mcp start/status/stop         管理后台 HTTP 服务
qqmusic-mcp install --client codex    注册 Codex MCP
qqmusic-mcp config --client <client>  输出客户端配置
qqmusic-mcp logout                    删除 DPAPI 登录缓存
qqmusic-mcp uninstall [--purge]       移除 Codex 注册，可选退出登录
qqmusic-mcp token                     生成 HTTP Bearer Token
```

## 数据与安全

本地数据位于 `%LOCALAPPDATA%\QQMusicOrganizer`：

- `exports/`：歌曲快照、CSV 和摘要。
- `plans/`：草稿、冻结计划、预览和 SHA-256。
- `runs/`：写入及回滚日志。
- `operations/`：通用歌单操作日志，可用于审计和人工回溯。
- `capabilities.json`：最近一次写入探针结果。
- `session.dpapi`：DPAPI 加密后的登录态。

安全边界：

- 标准模式使用 stdio，不开放网络端口。
- HTTP 模式仅监听 localhost 并校验 Bearer Token。
- 写回前必须完成创建、加歌、读取、移除和删除探针。
- 每批最多写入 20 首，并在写后回读确认。
- 同名歌单不唯一时停止，避免写错目标。
- “我喜欢”永远不是写入、删除或回滚目标。
- 通用工具的每次写操作都会记录日志；需要撤销时优先使用整理计划工作流的回滚能力。
- Cookie、令牌和完整认证错误不进入日志。

详见 [SECURITY.md](SECURITY.md)。

## 限制

- 本项目不是腾讯或 QQ 音乐官方产品，也与其无关联。
- QQ 音乐网站接口、登录流程或风控变化可能导致功能失效。
- MCP 提供数据和安全写入工具，不内置大模型；分类质量取决于使用它的 AI 客户端。
- 部分下架、地区限制或异常歌曲可能进入“待整理”。
- 歌词和歌曲详情是否可用取决于 QQ 音乐当前的版权和接口返回。
- 回滚不会恢复 QQ 音乐服务端自身的历史状态，只处理本项目运行日志记录的新增内容。

## 故障排查

查看 MCP 是否安装：

```powershell
Get-Command qqmusic-mcp
qqmusic-mcp --help
```

登录窗口被关闭：重新触发一次 MCP 调用，或执行 `qqmusic-mcp logout` 后重试。

HTTP 服务未就绪：

```powershell
qqmusic-mcp status
Get-Content "$env:LOCALAPPDATA\QQMusicOrganizer\service.err.log" -Tail 50
```

写入失败：不要绕过探针。保留本地计划和运行日志，更新到最新版后重新执行 `qqmusic_probe_write`。

## 许可证

[MIT](LICENSE)
