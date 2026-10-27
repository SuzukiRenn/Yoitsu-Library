# 脚本索引

按要完成的任务选择脚本。当前功能与版本边界先看[项目现状](../项目文档/项目现状与版本差异.md)，完整使用流程见[使用指南](../项目文档/功能与使用指南.md)。网站功能修改看[业务目录](../booklist/)，页面修改看[模板](../templates/booklist/)和[静态资源](../static/)。

## 哔哩轻小说采集与审核

| 文件 | 用途 |
| --- | --- |
| [collect_bilinovel_catalog.py](collect_bilinovel_catalog.py) | 发现目录、串行获取详情、保存检查点，支持有限网络重试和停止标记。 |
| [bili_review_server.py](bili_review_server.py) | 本机审核页面服务；只读已有书库，另存候选和人工结论。 |
| [bili_review_ui/](bili_review_ui/) | 审核页面的HTML、CSS与JavaScript。 |
| [bili_metadata.py](bili_metadata.py) | 公开详情解析、中文匹配键及基础候选比对。 |
| [bili_match_policy.py](bili_match_policy.py) | 自动排重、人工审核与新增候选分流规则。 |
| [bili_editions.py](bili_editions.py) | Web、文库、出版社及其他类型的识别和展示。 |

开始采集、继续暂停队列、选择已有书库和查看保存位置，见[采集与人工审核手册](../项目文档/哔哩采集进度与人工审核页_2026-10-23.md)。这些工具不直接把采集结果写进正式书库。

离线回归测试，无需访问来源网站：

```powershell
.venv/Scripts/python.exe -B scripts/test_bili_bootstrap.py
.venv/Scripts/python.exe -B scripts/test_bili_transport_retry.py
.venv/Scripts/python.exe -B scripts/test_bili_match_policy.py
.venv/Scripts/python.exe -B scripts/test_bili_editions.py
node --check scripts/bili_review_ui/app.js
```

## 文库8来源维护

| 文件 | 用途 | 对照文档 |
| --- | --- | --- |
| [probe_wenku8.py](probe_wenku8.py) | 有请求数量上限的网络和来源规则诊断。 | [来源适配代码](../integrations/wenku8/) |
| [discover_wenku8.py](discover_wenku8.py) | 发现文库8书目编号。 | [限速采集与首批建库](../项目文档/限速元数据采集与首批建库_2026-10-19.md) |
| [collect_wenku8.py](collect_wenku8.py) | 分批采集来源元数据。 | [协调并行采集](../项目文档/协调并行采集.md) |
| [update_wenku8.py](update_wenku8.py) | 每日增量更新入口。 | [每日增量更新](../项目文档/文库8每日增量更新.md) |
| [cache_wenku8_covers.py](cache_wenku8_covers.py) | 获取和维护本地封面缓存。 | [封面本地缓存](../项目文档/封面本地缓存_2026-10-20.md) |
| [sync_catalog.ps1](sync_catalog.ps1) | 书库同步操作入口。 | [首批作品与封面服务器同步](../项目文档/首批作品与封面服务器同步_2026-10-20.md) |

## 部署、备份与验证

| 文件 | 用途 |
| --- | --- |
| [predeploy.ps1](predeploy.ps1) / [server_preflight.ps1](server_preflight.ps1) | 预部署流程与服务器预检。 |
| [check_production.py](check_production.py) / [predeploy_health.py](predeploy_health.py) | 环境配置与服务健康检查。 |
| [sqlite_backup.py](sqlite_backup.py) / [pull_server_backup.ps1](pull_server_backup.ps1) | SQLite一致性备份与服务器备份拉取。 |
| [deploy_source_updates.ps1](deploy_source_updates.ps1) | 每日来源更新的部署配套操作。 |
| [prepare_https.py](prepare_https.py) / [configure_certificate.ps1](configure_certificate.ps1) | HTTPS配置和证书准备。 |
| [production_tasks.py](production_tasks.py) | 生产运行维护命令。 |
| `test_*.py` / `test_*.mjs` | 专项离线、交互和部署验证脚本，运行条件依脚本及对应手册。 |

涉及服务器的脚本按[部署手册](../项目文档/服务器预部署操作手册_2026-10-19.md)使用，不是本地启动网站的必要步骤。脚本执行记录属于本机运行数据，源码提交不会自动触发部署。

## 浏览体验与验收回归入口

| 验证 | 覆盖 |
| --- | --- |
| `node scripts/test_public_ui.mjs` | 写入、失败重试、轮询及嵌套作品引用。 |
| `node scripts/test_live_shelf.mjs` | 保留卡片标签的增删/清空及交互节点保留。 |
| `node scripts/test_shelf_sort.mjs` | 即时搜索、标签/作者链接、筛选及浏览历史。 |
| `node scripts/test_shelf_view.mjs`、`test_shelf_view_motion.mjs` | 跨板块视图偏好、切换动效及降级。 |
| `node scripts/test_appearance.mjs`、`test_reading_preferences.mjs` | 明暗、颜色、减少动效和数字显示偏好。 |
| `booklist.tests.test_approved_snapshots` | 审核获批/合并完整快照及已有历史保留。 |

Python业务回归通过 `manage.py test` 运行；先设置 `BOOKLIST_ENV=test`、独立 `BOOKLIST_DATA_DIR`，不能连接实际书库。完整命令及身份版旧匿名夹具开关见[首页开发验证](../README.md#开发验证)。Node专项脚本使用本机安装的Node运行；按测试文件说明确认运行条件，不把服务器部署命令当作本地测试。

## 真实 DOM 标签焦点回归

`test_live_shelf.mjs` 是保留卡片与外部控件的快速冒烟检查，不覆盖浏览器锚点断开后的真实焦点。FA-05专项请在当前工作树运行：

```powershell
python -B scripts/serve_live_shelf_dom.py --port 8984
```

打开终端给出的本机地址。测试页 `scripts/fixtures/live-shelf-focus.html` 直接调用本工作树的 `createShelfRefresh.apply`，使用浏览器真实元素和 `document.activeElement` 检查44个场景：公共/主题、默认/减少动效、其他标签增删/重排、当前标签删除的邻近回退、无标签回到作品标题、按钮/搜索框焦点、卡片移除与空书架。滚动位置在非零处检查。页面结果为 `failed: 0` 才通过；原Node冒烟全绿不能替代此检查。

该服务器固定回环监听，只提供合成测试页和公开ES模块，不读取数据库或暴露运行目录。完成后关闭终端停止服务。此同步函数测试不替代实际书架的自然轮询浏览器复验，后者须使用独立合成库。

## 本机备份保留策略

`pull_server_backup.ps1` 每次成功完成服务器归档下载和隔离恢复验证后，自动调用 `retain_offsite_backups.py`。最近7天保留全部，其前4个7天窗口各留最新一份，最新有效备份始终保留；异常文件不自动删除。现有Windows计划任务直接调用此脚本，无需重新安装。

预览：`python -B scripts/retain_offsite_backups.py --directory backups/server`。仅在新备份验证后使用 `--apply --after-backup <归档绝对路径>`。离线回归：`python -B scripts/test_offsite_retention.py`，使用专用临时合成库，不接触真实备份。详见[保留策略](../项目文档/本机备份保留策略_2026-10-27.md)。
