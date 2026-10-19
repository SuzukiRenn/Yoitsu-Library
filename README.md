# 共享书单

当前交付基础服务、离线导入、本地候选查询、公共推荐和月度活动参与。公共书单与月度推荐分开导航、页面、统计及写入范围；推荐和理由编辑均为独立页面。管理员后台、治理及受保护的合并/撤销已接入。服务器私有预部署已验收，尚未公开上线；DeepSeek 辅助仍待后续。

界面只保留必要文字，不增加标语、装饰英文或重复提示；后续页面遵守 `项目文档/界面文案约定_2026-10-19.md`。

产品语义更新：公共「+1」表示“我也推荐”，点赞表示“这本书很好”，两者独立。首次推荐者计 1 人，同一匿名身份不重复计数；空理由仍计推荐，清空理由不减少推荐人数。首页为公共书单，推荐在 `/recommend/`；活动推荐在 `/activities/<id>/recommend/`。输入后自动查询数据库候选，显示对应范围的推荐人数和本人状态，选择后补全标题。此独立页面约定取代阶段 4 弹窗设计，见 `项目文档/月度推荐与独立推荐页修正设计_2026-10-19.md`。

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

Windows x64 / Python 3.14 使用以下解析依赖锁；Linux amd64 / Python 3.14 的运行与解析依赖锁见 `requirements/production-linux-amd64-cp314.lock`，已通过服务器容器回归。服务器在线采集路线仍需单独验收。先安装解析依赖，再迁移：

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

书库 GET 接口不要求匿名 Cookie：`/api/v1/catalog/search/?q=书名&limit=10`（最多 20 个候选）、`/api/v1/catalog/works/<id>/`；独立只读目录入口为 `/catalog/`，详情为 `/works/<id>/`。目录包含尚未进入公共书单的可见作品，返回 `is_public` 区分收录状态。搜索结果必须由用户确认，不自动关联、创建或合并。隐藏、待核查和已合并的非正式记录不可查询；原始输入不自动作为别名。封面暂只展示安全 HTTPS 地址，其他使用占位。

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

本地 Git → [GitHub 仓库 SuzukiRenn/shared-booklist](https://github.com/SuzukiRenn/shared-booklist) → 租用 Ubuntu 服务器部署指定提交。用户于 2026-10-19 明确确认保留公开仓库，并允许上传已审查的代码和文档；此决定更新早期文档的私有仓库约定，详见《GitHub公开仓库确认与首次上传范围_2026-10-19》。服务器已完成私有预部署，未开放公网访问。提交须审查忽略与暂存清单，尤其是旧浏览器 profile、完整历史 HTML、旧虚拟环境和运行产物；只选择去敏必要样本，不能直接 `git add .` 后推送。

生产必须设置 `BOOKLIST_ENV=production`、随机长 `BOOKLIST_SECRET_KEY`、明确 `BOOKLIST_ALLOWED_HOSTS`、代码目录之外的绝对 `BOOKLIST_DATA_DIR`。配置不会自动读取 `.env.example`；该文件只是示例。真实密钥、凭据、SQLite/WAL/SHM、封面、日志、备份不进 Git。私有预部署已配置 WSGI 服务、活动调度与一致性备份；HTTPS、受信反向代理及公网访问仍需单独验收，不能把 `runserver` 用于生产。

数据库后续升级通过新迁移。服务器采集与 DeepSeek 辅助仍按后续阶段实现；管理、治理及合并/撤销已实现，生产发布/恢复按阶段 7 验收。公共及活动推荐与点赞已有写入接口；字段覆盖与标签抑制已有内部审计服务，尚无管理页面。

## 公共推荐接口

`/` 为公共书单，按推荐人数降序、首次收录时间降序、ID 升序。GET `/api/v1/public/works/?page=1&page_size=20&tag=1&tag=2` 返回作品、独立计数、当前身份状态及有效标签计数；标签同时满足，单页最多 100。只有公开正式作品进入列表，推荐归零仍保留收录状态。

所有公共写入先取得 CSRF 和匿名身份，另须 `Idempotency-Key`（16—200 字符，建议 UUID）；同一键重试必须保持内容相同。限流为每身份 60/分钟、每 IP 120/分钟。客户端在不确定网络结果时复用同一键，401 时不自动换身份重试。每身份实际指匿名 Cookie；清除 Cookie 会失去旧贡献的管理权限，并非实名去重。

| POST 路径 | 严格 JSON 字段 | 作用 |
|---|---|---|
| `/api/v1/public/recommendations/` | `work_id`（整数或 null）、`title`、`reason`、`confirmed_unmatched`（兼容旧客户端的布尔字段，现固定 false） | 选择已有作品或提交无候选的新书名，并建立个人推荐关系；理由可空 |
| `/api/v1/public/works/<id>/recommend/` | `enabled`（布尔） | +1 / 撤回推荐 |
| `/api/v1/public/works/<id>/like/` | `enabled`（布尔） | 独立点赞 / 取消 |
| `/api/v1/public/works/<id>/reason/` | `reason` | 编辑 / 清空自己的理由，不改变推荐或点赞 |

GET `/api/v1/public/works/<id>/reasons/?page=1&page_size=20` 分页返回公开理由。公共列表每部最多展示两条。用户编辑不能解除隐藏/失效治理；被隐藏的理由改写或清空时保留历史原文。原公共 Vote 与非空理由在迁移 0005 中按明确身份去重迁入推荐关系，不迁成点赞；原表保留。已有收录时间但无明确身份的数据不凭空补首次推荐者。

推荐页自动补齐候选使用 GET `/api/v1/recommend/candidates/?q=书名`；活动范围另加 `activity=<id>`。查询结果区分 `in_scope`、`recommended`、`recommendation_count` 和当前身份的理由；这些字段不会跨公共/活动范围取值。查询中、失败或尚未选择候选时不能把数据库候选当作零条直接提交。公共理由编辑入口为 `/recommend/?work=<id>&edit=1`。

未选作品 ID 时在写事务内复查当前标题、有效别名及本地候选；找到可见候选时返回 409，必须选择已有作品，普通提交不提供“作为新作品提交”的绕过入口。`confirmed_unmatched=true` 也不能放行。相似候选不等于同一身份，系统不自动关联或合并；无法确认时需管理员核查。检索失败也不能按零候选创建。未知身份仅与隐藏/待核查作品同名时仍按已确认 D7 进入待核查，不公开、不计公共统计；明确指向隐藏作品的 ID 则拒绝写入。

运行 `scripts/verify_stage4.py` 在独立 `.runtime/public_verification/` 数据库执行完整测试、离线样本导入、前端写入协议测试和真实迁移往返验证。`scripts/verify_public_migration.py` 单独验证旧公共 +1 / 理由去重、取消排除、零点赞及迁移往返完整性，始终使用新建测试库。浏览器预览贡献均为合成测试数据，不进入 Git 或生产。

## 月度推荐

`/activities/` 分组展示本期、待开始和往期，`/activities/<id>/` 为单期列表；时间按 Asia/Shanghai 显示。每期使用独立快照、推荐、理由和点赞，按活动赞降序、有效推荐人数降序、快照时间升序、快照 ID 升序排序。活动推荐确保公共收录，**不自动增加公共推荐人数、公共点赞或活动点赞**；活动理由默认不复制，主动勾选且当前没有公共理由时才复制，不覆盖已有内容。

普通活动写入继承公共写入的身份、CSRF、严格类型、幂等及限流协议。所有路径都绑定明确的活动 ID，进行中且服务器时间在截止之前才允许；暂停、结束、取消均只读。推荐与赞都归零退出活动，须重新推荐才可返回；不能靠旧链接点赞创建快照或唤回。重新推荐复用快照，来源更新不改快照。

| 路径 | 方法 / JSON | 作用 |
|---|---|---|
| `/api/v1/activities/<id>/` | GET `page,page_size` | 本期可见快照、独立计数及本人状态 |
| `/api/v1/activities/<id>/recommendations/` | POST 公共推荐四字段 + `copy_public_reason` 布尔 | 新增/更新本期推荐 |
| `/api/v1/activities/<id>/works/<work>/withdraw/` | POST `{}` | 撤回自己的本期推荐 |
| `/api/v1/activities/<id>/works/<work>/like/` | POST `enabled` 布尔 | 活动点赞/取消 |
| `/api/v1/activities/<id>/works/<work>/reason/` | POST `reason` | 编辑本期理由 |
| `/api/v1/activities/<id>/works/<work>/reasons/` | GET `page,page_size` | 分页公开理由 |
| `/api/v1/admin/activities/` | POST `title,theme,start_at,end_at` | 有效管理员创建草稿 |
| `/api/v1/admin/activities/<id>/update/` | POST 上述四字段 + `reason` | 修改/延期，检查时段及终态锁定 |
| `/api/v1/admin/activities/<id>/action/` | POST `action,reason` | schedule/start/pause/resume/end/cancel，按状态矩阵执行 |

管理员时间必须为带时区 ISO 时间（示例 `2026-11-01T00:00:00+08:00`），普通用户无创建活动权限。安排、开始和延期在写事务内检查冲突，暂停仍占时段；提前结束释放剩余时段。活动数据与审计/转换事件同事务保存。命令 `manage.py advance_activities` 幂等处理计划开始与截止，活动页面及写入前也会补处理；离线错过完整时段直接结束、不补时，分别保存业务生效及实际处理时间。私有预部署已配置独立调度容器，每 30 秒处理一次。

隐藏同名未知作品的活动提交存入迁移 0006 的 `PendingActivitySubmission`，保留提交归属、理由和复制选择；不创建有效推荐/快照，也不增加公共推荐关系。管理员可在 `/manage/` 核查：批准独立作品或确认合并到已有作品；过期活动不会自动回填，须管理员明确审核并留原因，历史更正标记 `corrected_at`。治理、合并及符合保护条件的撤销已提供后台操作。


## 管理员后台

入口 `/manage/`，登录 `/manage/login/`，沿用现有管理员会话、CSRF 和停用会话校验。可管理待核查作品、人工核查后的新建、隐藏/恢复作品与理由、推荐资格与点赞纠错、月度活动、作品合并及受保护的撤销、分页审计。操作需填写原因；不会提供公开注册或默认账户。

合并需预览并逐项选择冲突；同身份推荐/赞去重，理由按完整更新时间判定，已有快照保留。撤销遇到后续内容变更或依赖时停止，避免覆盖新贡献。详情及验证记录见 [管理员后台实现与验收](项目文档/管理员后台实现与验收_2026-10-19.md)。后台代码已纳入私有预部署，真实管理员尚未初始化，公网访问未开放。


## 私有预部署

2026-10-19 已验收私有预部署，运行提交为 `d585d79825dbf5e27feac12e3abf52821f12fc0d`。服务器 Linux 的 165 项应用测试与 3 项备份测试通过，迁移、私有 HTTP 健康检查和独立恢复演练通过；只读复核确认应用健康、调度及备份成功、三个容器零重启、无端口映射、内部网络及版本指针正确。运行环境为 Python 3.14.8 / SQLite 3.53.2。

Docker/Compose 模板、固定运行镜像和 Linux 依赖锁、备份与调度详情见 [服务器预部署操作手册](项目文档/服务器预部署操作手册_2026-10-19.md)。配置及运行数据均留在服务器，不导入开发库或示例作品。用户选择先准备公网 IP + HTTPS，见[上线准备](项目文档/公网IP与HTTPS上线准备_2026-10-19.md)；公开访问前提、TLS、静态代理、真实管理员初始化和异地备份另行验收。
