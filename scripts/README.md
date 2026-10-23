# 脚本索引

按要完成的任务选择脚本。网站功能修改看[业务目录](../booklist/)，页面修改看[模板](../templates/booklist/)和[静态资源](../static/)。

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
