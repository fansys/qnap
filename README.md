# Emby + Tailscale QNAP Repository

本目录即 GitHub 仓库根。Actions 并行同步 Emby / Tailscale 包到本仓 Release，再统一生成
QNAP App Center 软件源。

## Actions

| Workflow | 触发 | 说明 |
|---|---|---|
| `sync-qnap-repo.yml` | 定时 + 手动 | Emby / Tailscale **并行**，再统一写 feed |
| `sync-emby.yml` | 仅手动 | 只构建 Emby；可指定 `version` / `force` |
| `sync-tailscale.yml` | 仅手动 | 只镜像 Tailscale；可指定 `version` / `force` |

```text
# 定时 / 全量手动
┌─────────────┐   ┌─────────────────┐
│ Sync Emby   │   │ Sync Tailscale  │   ← 并行
└──────┬──────┘   └────────┬────────┘
       └─────────┬─────────┘
                 ▼
        Update repo feeds

# 单独手动（Sync Emby / Sync Tailscale）
Sync Xxx  →  Update repo feeds
```

## CLI

```bash
python3 sync_release.py emby --repo <owner>/<name> --version 4.10.1.0 --force --dry-run
python3 sync_release.py tailscale --repo <owner>/<name> --version 1.92.3 --force --dry-run
python3 sync_release.py feeds --repo <owner>/<name>
python3 sync_release.py all --repo <owner>/<name> --dry-run   # 本地串行便捷入口
```

| 子命令 | 作用 |
|---|---|
| `emby` | 仅同步 Emby（产出 `out/signatures-emby.json`） |
| `tailscale` | 仅镜像 Tailscale stable（产出 `out/signatures-tailscale.json`） |
| `feeds` | 合并签名缓存，写 `repo.xml` / `repo_pre.xml` |
| `all` | 本地串行：emby → tailscale → feeds |

## GitHub 加速（repo.xml 下载根）

默认 preset 为 **`env`**：先读环境变量，没有配置则兜底 **`none`**（不加速）。

| preset | 行为 |
|---|---|
| `env`（默认） | 读 `FEED_ACCELERATE_PREFIX`，否则读 `FEED_ACCELERATE_PRESET`；都没有 → 不加速 |
| `none` | 强制不加速 |
| `ghproxy` / `ghfast` / ... | 使用 `config.json` 里对应前缀 |

```json
{
  "accelerate": {
    "enabled": true,
    "preset": "env",
    "presets": {
      "ghproxy": "https://ghproxy.net/",
      "ghfast": "https://ghfast.top/"
    }
  }
}
```

GitHub Actions：**Settings → Variables** 配置：

- `FEED_ACCELERATE_PREFIX`：例如 `https://ghproxy.net/`（推荐）
- `FEED_ACCELERATE_PRESET`：可选，如 `ghfast`（PREFIX 未设时生效）
- `FEED_ACCELERATE_ENABLED`：可选

这些 Variable 由 workflow 在调用 `update-feeds` 时注入为环境变量（composite action 内不能直接写 `vars.*`）。

手动参数 `accelerate_preset` 默认就是 `env`。

新增软件源应用：实现 `FeedProvider` 并在 `config.json` → `feed.providers` 注册名称（见 `ci/providers.py`）。

## 目录

| 路径 | 说明 |
|---|---|
| `sync_release.py` | CLI 入口 |
| `config.json` | 加速预设、feed providers |
| `ci/` | 解耦模块 |
| `ci/urls.py` / `ci/config.py` | 加速 URL 与配置 |
| `ci/providers.py` / `*_feed.py` | 可扩展 feed 提供方 |
| `ci/emby.py` / `ci/tailscale.py` | 各自发布逻辑 |
| `signing/*.pem` | Emby 自签材料（完整性，不过 QoS） |
| `repo.xml` / `repo_pre.xml` | QNAP 软件源 |
| `out/` | 构建临时目录与签名片段（不入库） |
| `.github/workflows/` | 并行全量 + Emby/Tailscale 手动 |
| `.github/actions/update-feeds/` | 三个 workflow 共用的写源 + 提交步骤 |

## Release tag

| 应用 | Tag |
|---|---|
| Emby | `emby-4.10.1.0` |
| Tailscale | `tailscale-1.104.1` |

## 订阅

```text
https://raw.githubusercontent.com/<owner>/<repo>/<branch>/repo.xml
https://raw.githubusercontent.com/<owner>/<repo>/<branch>/repo_pre.xml
```

QNAP 约定：同一 `internalName` 仅一条 `<item>`；Tailscale 一条 item 下多个 `<platform>`。
