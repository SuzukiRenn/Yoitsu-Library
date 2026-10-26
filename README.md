<p align="center">
  <img src="static/logo.png" width="88" height="88" alt="ヨイツ図書館 Logo">
</p>

<h1 align="center">ヨイツ図書館</h1>
<p align="center">Yoitsu Library</p>

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

| 共同荐书 | 浏览与发现 | 个性化阅读 |
| :--- | :--- | :--- |
| **公共书架与主题推荐**<br>推荐、作品点赞、理由赞同独立统计，主题有独立书目和历史快照。 | **即时搜索与筛选**<br>输入即搜索，支持作者、别名、多标签全部/任一匹配及“推荐过 / 点赞过”。 | **卡片 / 列表视图**<br>两板块共用视图偏好；列表隐藏理由预览，浏览更紧凑。 |
| **推荐理由**<br>可选填写，支持剧透保护；输入 `/` 选择并引用其他作品。 | **连续选书**<br>按当前板块随机推荐，详情可上下本浏览，并返回原筛选与滚动位置。 | **外观设置**<br>明暗模式、五种柔和主题色、减少动效、隐藏推荐点赞数字。 |
| **管理与分享**<br>待核查、来源核对、合并与审计，支持导出主题总结图片。 | **资料与版本**<br>作者和标签可点击；详情展示版本资料，仅 Web 作品在列表书名后标记。 | **局部更新**<br>搜索、筛选、排序和标签轮询采用局部更新，兼容手机与键盘。 |

本分支为匿名版 `main`，参与权限由当前浏览器 Cookie 识别；无需读者账户。身份版独立维护在 [`codex/identity-booklist`](https://github.com/SuzukiRenn/shared-booklist/tree/codex/identity-booklist)，两版使用独立数据库。

详细流程见[功能与使用指南](项目文档/功能与使用指南.md)，版本边界和最近发布记录见[项目现状与版本差异](项目文档/项目现状与版本差异.md)。

## 怎样参与

**搜索作品 → 推荐或点赞 → 分享理由 → 参与活动**

<details>
<summary>查看参与方式与匿名身份规则</summary>

1. 在公共书架或活动页面点击“推荐”，先搜索并选择已有作品。
2. 没有匹配作品时，填写书名，并可补充作者、简介和标签。相似作品会要求确认，避免重复收录。
3. 推荐理由可以留空。“推荐”表示你也推荐这本书，“点赞”是另一项独立评价。
4. 从作品卡片进入详情页，查看资料和其他人的推荐理由，也可以赞同喜欢的理由。

公共书架与每期活动分别统计。活动默认同时建立公共推荐，也可由管理员设置为“仅推荐到本期活动”。仅本期模式不自动增加公共推荐或公共理由；同步公共推荐时，同一浏览器身份对同一本作品不会重复计数，撤回活动推荐不会自动撤回公共推荐。

推荐数归零后，作品退出对应书单，结束这一轮互动；往期活动保留历史快照。手工作品在所有当前书单均归零后，再次推荐需要重新填写资料。

个人推荐、点赞和编辑权限由浏览器 Cookie 识别。清除 Cookie、使用无痕窗口或更换设备后，不会自动继承原来的操作权限。

</details>

## 管理员功能

后台入口为 `/manage/`，负责书目、主题推荐、反馈与来源核查。

<details>
<summary>查看管理能力</summary>

后台入口为 `/manage/`，包括作品库、公共书架管理、主题推荐、待核查作品、来源身份核对、合并记录和操作审计。

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

明确同版本重复的条目自动排除重复入库；疑似重复交人工确认，无匹配的进入新增候选。人工判断优先。**只有最终确认新增的作品才入库并下载封面**，重复条目不进入正式库。审核页仅监听本机，暂存数据、审核结论和封面文件不提交GitHub。

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

最近一次已核验服务器发布为 **2026-10-25**，两版均已包含验收修正；这不是今天重新探测线上状态的结论。服务仍为私有访问，GitHub 文档更新不会自动部署。版本和验证边界见[项目现状与版本差异](项目文档/项目现状与版本差异.md)。

应用已完成服务器私有部署与验证，**目前尚未开放公众访问**。仓库中的部署工具提供 Docker Compose、活动调度、数据库备份和恢复校验；已有域名 HTTPS 私有联调与 SSH 访问方式；正式公开访问需另行核实上线条件和入口配置。开发用 `runserver` 不用于生产。

作品来源元数据支持校验、分批导入和断点恢复，封面支持本地缓存。项目用于整理书目与推荐，不提供小说正文下载。

## 常用维护文档

| 文档 | 内容 |
| --- | --- |
| [功能与使用指南](项目文档/功能与使用指南.md) | 阅读、筛选、推荐、理由、个人偏好和管理流程。 |
| [项目现状与版本差异](项目文档/项目现状与版本差异.md) | 两版边界、最近核验发布、已知限制和数据保护。 |
| [文档导航](项目文档/README.md) | 按阅读、管理、采集和运维任务查找专题。 |
| [脚本索引](scripts/README.md) | 本地审核、离线测试、备份与发布工具入口。 |

历史设计和验收记录保留原文件名与当时结论；当前使用方式先看指南和对应专题，不能用旧记录中的“未部署”判断现在的部署状态。

## 开发验证

测试需要 Node.js；使用独立测试目录，避免连接实际运行数据：

```powershell
$env:BOOKLIST_ENV='test'
$env:BOOKLIST_DATA_DIR=Join-Path (Get-Location) '.runtime/local-tests'
.\.venv\Scripts\python.exe -B manage.py test booklist.tests --noinput
node scripts/test_public_ui.mjs
node scripts/test_live_shelf.mjs
# 按需运行其余 scripts/test_*.mjs 专项回归。
```

数据库、Cookie、密钥、日志、备份和封面缓存均不应提交到仓库。
