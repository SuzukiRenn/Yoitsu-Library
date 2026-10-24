<p align="center">
  <img src="static/logo.png" width="88" height="88" alt="共享书单 Logo">
</p>

<h1 align="center">共享书单</h1>

<p align="center"><strong>一起推荐，发现下一本想读的书。</strong></p>

<p align="center">
  面向读书群与小说爱好者的共同荐书网站。<br>
  汇集推荐、分享理由，也围绕一个主题举办荐书活动。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.14-242424?style=flat-square&amp;labelColor=555555" alt="Python 3.14">
  <img src="https://img.shields.io/badge/Django-5.2-242424?style=flat-square&amp;labelColor=555555" alt="Django 5.2">
  <img src="https://img.shields.io/badge/SQLite-数据库-242424?style=flat-square&amp;labelColor=555555" alt="SQLite 数据库">
  <img src="https://img.shields.io/badge/JavaScript-原生模块-242424?style=flat-square&amp;labelColor=555555" alt="原生 JavaScript 模块">
</p>

<p align="center">
  <a href="#可以做什么">功能一览</a> ·
  <a href="#本地运行">本地运行</a> ·
  <a href="#书目采集与审核">采集与审核</a> ·
  <a href="#目录说明">目录说明</a> ·
  <a href="项目文档/README.md">文档索引</a>
</p>

---

## 可以做什么

| 共同荐书 | 主题活动 | 选书参考 |
| :--- | :--- | :--- |
| **公共书单**<br>一起推荐和点赞，按标签、热度或时间发现作品。 | **限时推荐活动**<br>围绕主题收集书目，独立统计，回顾往期活动。 | **作品资料**<br>查看封面、作者、简介与标签，搜索已有作品或补充新书。 |
| **推荐理由**<br>分享喜欢的原因，赞同理由，支持长文展开与剧透折叠。 | **群内分享**<br>导出带封面的活动总结图片，支持打包下载。 | **轻量交互**<br>推荐与点赞近实时更新，兼容桌面和手机。 |

普通参与无需注册，管理员使用独立后台。推荐、作品点赞与理由赞同分别统计。

## 怎样参与

**搜索作品 → 推荐或点赞 → 分享理由 → 参与活动**

<details>
<summary>查看参与方式与匿名身份规则</summary>

1. 在公共书单或活动页面点击“推荐”，先搜索并选择已有作品。
2. 没有匹配作品时，填写书名，并可补充作者、简介和标签。相似作品会要求确认，避免重复收录。
3. 推荐理由可以留空。“推荐”表示你也推荐这本书，“点赞”是另一项独立评价。
4. 从作品卡片进入详情页，查看资料和其他人的推荐理由，也可以赞同喜欢的理由。

公共书单与每期活动分别统计。参与活动会同时建立公共推荐，同一浏览器身份对同一本作品的公共推荐不会重复计数；撤回活动推荐不会自动撤回公共推荐。

推荐数归零后，作品退出对应书单，结束这一轮互动；往期活动保留历史快照。手工作品在所有当前书单均归零后，再次推荐需要重新填写资料。

个人推荐、点赞和编辑权限由浏览器 Cookie 识别。清除 Cookie、使用无痕窗口或更换设备后，不会自动继承原来的操作权限。

</details>

## 管理员功能

后台入口为 `/manage/`，负责书目、推荐活动、反馈与来源核查。

<details>
<summary>查看管理能力</summary>

后台入口为 `/manage/`，包括作品库、公共书单管理、推荐活动、待核查作品、来源身份核对、合并记录和操作审计。

- 创建、安排、暂停和结束活动，设置主题与起止时间。
- 核查作品，隐藏或恢复作品及理由，处理异常推荐和点赞。
- 核对疑似重复来源，预览作品合并，并在满足保护条件时撤销合并。
- 生成带封面的活动总结图片；同一份导出中的卡片保持统一高度，理由数量不会拉长卡片。

后台与前台有明确的往返入口。前台的“返回管理后台”和图片导出入口仅对已登录管理员显示。

</details>

## 本地运行

项目使用 **Django + SQLite**，前端为服务端模板、CSS 和原生 JavaScript 模块，无需单独启动前端服务。

需要 Python 3.14，且 Python 实际链接的 SQLite 版本不低于 3.51.3。以下为 Windows PowerShell 示例：

```powershell
git clone https://github.com/SuzukiRenn/shared-booklist.git
cd shared-booklist
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements/parsing-win_amd64-cp314.lock

$env:BOOKLIST_ENV='development'
.\.venv\Scripts\python.exe -B manage.py migrate --noinput
.\.venv\Scripts\python.exe -B manage.py init_admin owner
.\.venv\Scripts\python.exe -B manage.py runserver 127.0.0.1:8000 --noreload
```

初始化时在终端设置管理员密码，没有默认密码。启动后访问：

- 普通页面：<http://127.0.0.1:8000/>
- 管理员后台：<http://127.0.0.1:8000/manage/>

首次运行的书库为空。开发数据默认保存在 `.runtime/`，也可通过 `BOOKLIST_DATA_DIR` 指定独立目录。线上数据库、用户贡献和封面文件不随源码分发。

## 书目采集与审核

网站书库与来源暂存库分开维护，采集成功不代表已正式入库。

| 来源或工具 | 当前用途 | 入口 |
| --- | --- | --- |
| 文库8 | 元数据采集、校验、导入及每日增量更新。 | [每日更新说明](项目文档/文库8每日增量更新.md) |
| 哔哩轻小说 | 断点续采元数据，补充已有来源未收录的作品。 | [采集与审核手册](项目文档/哔哩采集进度与人工审核页_2026-10-23.md) |
| 本地审核页 | 查看实时进度、重复候选、新增候选及人工审核结果。 | `scripts/bili_review_server.py`，启动后默认访问 `http://127.0.0.1:8899/`。 |

明确重复的条目自动排除重复入库；疑似重复交人工确认，无匹配的进入新增候选。人工判断优先。**只有最终确认新增的作品才入库并下载封面**，重复条目不进入正式库。审核页仅监听本机，暂存数据、审核结论和封面文件不提交GitHub。

## 目录说明

| 路径 | 用途 |
| --- | --- |
| [booklist/](booklist/) | 书目、推荐、点赞、活动、管理后台等业务逻辑，包含数据模型、迁移和业务测试。 |
| [config/](config/) | Django运行配置、路由、代理及服务入口。 |
| [templates/booklist/](templates/booklist/) | 网站页面模板。 |
| [static/](static/) | 网站CSS、JavaScript及静态资源。 |
| [integrations/wenku8/](integrations/wenku8/) | 文库8元数据解析、校验和来源适配。 |
| [scripts/](scripts/README.md) | 采集、人工审核、备份、部署及验证工具。 |
| [deploy/](deploy/) | Docker、定时任务、证书和服务器运行配套文件。 |
| [requirements/](requirements/) | Python依赖及按环境固定的版本、校验值。 |
| [tests/](tests/) | 来源解析样本及专项验证材料；业务测试还位于booklist中。 |
| [tools/wenku8-directory-extension/](tools/wenku8-directory-extension/README.md) | 文库8目录辅助浏览器扩展。 |
| [项目文档/](项目文档/README.md) | 当前使用规则、维护手册与历史设计验收记录。 |
| [manage.py](manage.py) / [.env.example](.env.example) | Django命令入口 / 环境变量示例。 |

`.runtime/`、`backups/`等本机运行目录不随源码上传。更多操作入口见[脚本索引](scripts/README.md)和[文档索引](项目文档/README.md)。

## 部署与数据维护

应用已完成服务器私有部署与验证，**目前尚未开放公众访问**。仓库中的部署工具提供 Docker Compose、活动调度、数据库备份和恢复校验；正式公开访问还需完成域名入口及对应部署配置。开发用 `runserver` 不用于生产。

作品来源元数据支持校验、分批导入和断点恢复，封面支持本地缓存。项目用于整理书目与推荐，不提供小说正文下载。

## 常用维护文档

| 文档 | 内容 |
| --- | --- |
| [服务器部署](项目文档/服务器预部署操作手册_2026-10-19.md) | 运行环境、指定版本部署、调度与备份。 |
| [域名与 HTTPS](项目文档/域名接入预配置_2026-10-20.md) | 域名入口、证书与私有联调情况。 |
| [本地封面缓存](项目文档/封面本地缓存_2026-10-20.md) | 封面采集、缓存与备份。 |
| [协调并行采集](项目文档/协调并行采集.md) | 编号分配、统一限速、串行入库和检查点。 |
| [推荐理由点赞](项目文档/推荐理由点赞.md) | 理由展示和赞同规则。 |
| [退榜与重新推荐](项目文档/作品退榜与重新推荐规则.md) | 推荐归零、资料重填与历史快照。 |

阶段设计和历史验收材料保留在 [项目文档](项目文档/) 中；带日期的记录用于追溯当时状态，当前行为以代码和最新规则为准。

## 开发验证

测试需要 Node.js；使用独立测试目录，避免连接实际运行数据：

```powershell
$env:BOOKLIST_ENV='test'
$env:BOOKLIST_DATA_DIR=Join-Path (Get-Location) '.runtime/local-tests'
.\.venv\Scripts\python.exe -B manage.py test booklist.tests --noinput
node scripts/test_activity_export.mjs
```

数据库、Cookie、密钥、日志、备份和封面缓存均不应提交到仓库。
