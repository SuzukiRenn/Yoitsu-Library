# 共享书单

当前交付基础服务、离线导入及阶段 3 本地候选查询：数据库、匿名身份、管理员认证、元数据同步、书名/别名/模糊搜索和作品详情。首页为只读书库查询；推荐、点赞、活动、合并写入及 DeepSeek 辅助尚未开放。

界面只保留必要文字，不增加标语、装饰英文或重复提示；后续页面遵守 `项目文档/界面文案约定_2026-10-19.md`。

产品语义更新：公共「+1」表示“我也推荐”，点赞表示“这本书很好”，两者独立；不能沿用旧材料中的混称。首页应以公共推荐书单为主体，候选搜索辅助推荐提交。详见 `项目文档/公共推荐加一与点赞语义纠正_2026-10-19.md`；相关互动与首页调整尚未实现。

## 本地运行

需要受支持的 Python 3.14 及其实际链接的 SQLite >= 3.51.3；本机验证为 Python 3.14.6 / SQLite 3.53.2。不要升级或复用 `tests/wenku8_browser/.venv`，它用于保留旧采集回归。

```powershell
# 首次创建独立环境；如系统 TEMP 不可写，先指向项目 .runtime/tmp。
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple --require-hashes -r .\requirements\runtime.lock
$env:BOOKLIST_ENV='development'
.\.venv\Scripts\python.exe -B manage.py migrate --noinput
.\.venv\Scripts\python.exe -B manage.py init_admin owner
.\.venv\Scripts\python.exe -B manage.py runserver 127.0.0.1:8000 --noreload
```

初始化密码交互输入，不使用命令行密码参数；自动化可通过临时环境变量 `BOOKLIST_INITIAL_PASSWORD` 传入并在运行后清除。再次初始化不创建新账号或重置密码。首次管理员只可本地初始化，后续账号由已登录管理员创建。测试源码中的密码仅用于测试库。

遗失密码时由服务器操作者运行 `manage.py reset_admin_password <username>`，交互输入新密码（自动化可临时使用 `BOOKLIST_RESET_PASSWORD`）。该操作记录系统恢复审计并撤销全部旧会话，不启用已停用账号；没有公开重置接口。

开发数据默认 `.runtime/booklist.sqlite3`。`BOOKLIST_DATA_DIR` 可指定独立目录；测试使用该目录内的 `test_booklist.sqlite3`，不要将它指向生产目录运行测试。运行 `scripts/verify_stage1.py` 会新建独立验证目录，不使用开发或生产库。

```powershell
$env:BOOKLIST_ENV='development'
.\.venv\Scripts\python.exe -B manage.py check
.\.venv\Scripts\python.exe -B manage.py test booklist.tests --noinput
# 还需要 Node.js 来比较前后端文本黄金样例。
.\.venv\Scripts\python.exe -B scripts/verify_stage1.py
```

阶段 1 验证脚本包含本机回环 HTTP 请求；不访问文库8/DeepSeek，不启动 Chrome，不部署服务器。新证据保存在 `.runtime/verification/<时间>/`，不进入 Git。原工作区的历史采集材料保持不变；本仓库只包含运行所需的两个旧解析器及明确选出的元数据样本。

## 离线小样本导入

当前解析依赖锁仅验证 Windows x64 / Python 3.14；Linux 安装与服务器采集路线仍待验证。先安装解析依赖，再迁移：

```powershell
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple --require-hashes -r requirements/parsing-win_amd64-cp314.lock
$env:BOOKLIST_ENV='development'
.\.venv\Scripts\python.exe -B manage.py migrate --noinput
.\.venv\Scripts\python.exe -B manage.py import_wenku8 --manifest tests/fixtures/wenku8/manifest.json --max-items 1
# 用上一条输出的 batch_id 恢复；替换下方占位符。
.\.venv\Scripts\python.exe -B manage.py import_wenku8 --resume <batch_id>
.\.venv\Scripts\python.exe -B scripts/verify_stage2.py
```

命令只读本地 HTML 元数据，不访问来源网站、不下载封面或正文。每批最多 20 项，逐项事务，支持中断恢复；重复样本不重复创建作品/标签。`paused` 尚有待处理项，`completed` 全部成功，`completed_partial` 存在可选字段警告，`completed_with_errors` 存在失败项（退出非零）。请同时检查 counts、每项结果与警告；退出 0 不表示所有可选字段完整。

恢复绑定 manifest、输入文件和解析代码的指纹。修正内容后新建批次；仅对未改变的失败输入使用 `--resume <batch_id> --retry-failed`。无可信 ID/标准标题整项失败，可选字段失败不会清空旧值；不完整标签集合不会撤销旧关联。管理员覆盖与治理状态保留，导入不自动加入公共书单。阶段 2 验证证据写入 `.runtime/import_verification/<时间>/`，包括全套测试和真实样本对照；克隆后的旧文件保护计数只反映仓库内实际存在的文件。

## 有限网络检查

`python scripts/probe_wenku8.py --location local` 使用标准库执行至多两个无登录 GET：先读 robots.txt，规则可识别且允许时，再隔至少 10 秒读取固定作品详情。遇重定向、拒绝、异常或过大响应即停止，不自动重试、不保存页面，不写数据库。退出 0 只说明收到了 HTML 响应；登录页也可能返回 200，因此不能据此认定作品解析或采集路线已验收。

诊断记录响应头/首字节耗时、已收字节/块数、传输是否完整及 Content-Length 是否一致；超时仍保留已收到数据的计数，不将部分正文传给解析器。单次阻塞 I/O 超时 15 秒，总计 30 秒的预算在读取块之间检查，不能保证正在阻塞的调用恰好在第 30 秒退出。`--check-metadata` 可在本机已安装解析依赖时复用适配器做作品身份及字段诊断，不写数据库；可选值校验、完整导入和服务器解析均不因此视为通过。JSON 使用 ASCII 转义保存 Unicode，避免终端重定向损坏中文。

完整性诊断只忽略已定位在实际、带引号 href 属性内的缺实体分号警告，不忽略正文中的实体错误或其他解析错误。必须有真实显式结束标签；诊断过多时停止认证，避免恢复后的 DOM 掩盖截断。`document_diagnostics` 给出计数和类型，不输出原始 URL 或解析器消息。适配器代码改变后指纹会变化，旧批次按规则不直接恢复，应建立新批次。

需要显式进行一部固定作品的在线端到端验证时，运行 `.\.venv\Scripts\python.exe -B scripts/verify_online_sample.py`。它新建 `.runtime/online_import_verification/<时间与随机后缀>/data`，忽略传入的数据目录配置；先迁移、再遵循同一最多两个 GET 的预算，字段完整且通过统一校验后才写该验证库，再重复同步同一内存样本检查幂等，不为重复导入再次联网。它不保存原始页面、不写开发或生产数据库，也不是批量采集器。运行结果与迁移日志留在该证据目录。

服务器可用系统 Python 3 执行同一脚本并指定 `--location server`；该参数只标注操作者声明的运行位置，不自动连接服务器或证明位置。密码登录的执行方法见 `项目文档/服务器只读检查操作说明_2026-10-19.md`。连接地址、账号认证材料及实际检查输出不进入仓库。

## 基础 API

书库 GET 接口不要求匿名 Cookie：`/api/v1/catalog/search/?q=书名&limit=10`（最多 20 个候选）、`/api/v1/catalog/works/<id>/`；浏览器入口为 `/` 和 `/works/<id>/`。目录包含尚未进入公共书单的可见作品，返回 `is_public` 区分收录状态。搜索结果必须由用户确认，不自动关联、创建或合并。隐藏、待核查和已合并的非正式记录不可查询；原始输入不自动作为别名。封面暂只展示安全 HTTPS 地址，其他使用占位。

搜索优先原文精确，再做 NFKC/casefold 规范化、前缀、包含和有限模糊比较；不转换未知别名、不调用外部 AI。最多扫描 20000 个有效检索词，超限明确返回 503，不能将其当作没有匹配。工程阈值及未验证的容量边界见阶段 3 设计。搜索与目录读取分别按 IP 限制 60/分钟、120/分钟。应用服务不持久化搜索词；开发/代理访问日志可能包含查询串，部署时仍需配置日志口径。

运行 `scripts/verify_stage3.py` 会在 `.runtime/catalog_verification/` 新建独立库，执行全套测试/迁移检查并导入三份离线元数据用于预览，不联网采集。本地 `runserver` 由 staticfiles 提供静态文件；`.mjs` 在应用进程中明确设置 JavaScript MIME 类型，避免受 Windows 类型注册影响。生产静态文件由后续部署配置服务，不使用开发服务器上线。

先 GET `/api/v1/csrf/` 获取 `csrf_token` 并保留 Cookie；每次 POST 使用 `Content-Type: application/json` 和 `X-CSRFToken`。登录会轮换 CSRF，后续写入前重新获取。所有写入均同源校验，不能仅以持有链接替代 CSRF。

| 路径 | 方法 / JSON 字段 | 作用 |
|---|---|---|
| `/health/` | GET | 数据库和迁移就绪检查 |
| `/api/v1/identity/` | POST `{}` | 签发或续期匿名身份，不返回身份 ID/token 原文 |
| `/api/v1/identity/reset/` | POST `{}` | 撤销旧 token、签发新身份；旧贡献保留且不继承权限 |
| `/api/v1/admin/login/` | POST `username,password` | 独立管理员 Cookie，绝对 12 小时 |
| `/api/v1/admin/me/` | GET | 当前管理员名称 |
| `/api/v1/admin/logout/` | POST `{}` | 退出当前会话 |
| `/api/v1/admin/accounts/` | POST `username,password` | 已登录有效管理员创建同权账号 |
| `/api/v1/admin/accounts/<id>/disable/` | POST `{}` | 原子停用，不能停用最后一名有效管理员 |
| `/api/v1/admin/password/` | POST `current_password,new_password` | 修改本人密码并使全部旧会话失效 |

错误为 `{error: {code, message}}`，采用 400/401/403/404/405/409/413/415/429/503；未知字段或错误类型拒绝。应用不提供任意表编辑界面。CLI/ORM 管理更改也必须走业务服务保持审计，直接 SQL 的业务变化会留下 ChangeEvent，但不会自动生成足够的管理员操作说明。

数据库审计、变更事件、转换历史和合并明细为追加记录，SQL UPDATE/DELETE 会被触发器拒绝。因此 `manage.py flush` **不是受支持的运行清理方式**；不要为了清空开发库绕过保护去操作生产库。测试用专用库，数据库初始化和结构升级只使用迁移。`cleanup_security` 仅清到期会话/限流/幂等记录，不删除贡献。

## 发布边界

本地 Git → [GitHub 仓库 SuzukiRenn/shared-booklist](https://github.com/SuzukiRenn/shared-booklist) → 租用 Ubuntu 服务器部署指定提交。用户于 2026-10-19 明确确认保留公开仓库，并允许上传已审查的代码和文档；此决定更新早期文档的私有仓库约定，详见《GitHub公开仓库确认与首次上传范围_2026-10-19》。服务器尚未部署。提交须审查忽略与暂存清单，尤其是旧浏览器 profile、完整历史 HTML、旧虚拟环境和运行产物；只选择去敏必要样本，不能直接 `git add .` 后推送。

生产必须设置 `BOOKLIST_ENV=production`、随机长 `BOOKLIST_SECRET_KEY`、明确 `BOOKLIST_ALLOWED_HOSTS`、代码目录之外的绝对 `BOOKLIST_DATA_DIR`。配置不会自动读取 `.env.example`；该文件只是示例。真实密钥、凭据、SQLite/WAL/SHM、封面、日志、备份不进 Git。HTTPS、受信反向代理、WSGI 服务、调度与一致性备份在实际服务器验证后配置，不能把 `runserver` 用于生产。

数据库后续升级通过新迁移。服务器采集、DeepSeek 辅助、贡献写入、活动状态服务、治理管理界面及安全合并/撤销仍按后续阶段实现，生产发布/恢复按阶段 7 验收。字段覆盖与标签抑制已有内部审计服务，尚无管理页面。
