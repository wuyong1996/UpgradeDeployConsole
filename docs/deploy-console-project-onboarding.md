# 工程自动部署接入规范

适用版本：2026-09-24 前端补接与 MySQL 自动初始化版。旧版 `20260923-autopublish` 不支持自动建库，需更新完整面板和服务器适配器。

**正常流程不需要手工建库、建账号或执行 SQL。复用宝塔已有 MySQL，工程提供构建描述和迁移入口后，面板自动完成；资源归属、配置或迁移冲突时停止并提示。**

## 1. 首次使用

1. 安装新版面板，在“面板设置 → MySQL 自动部署”填写一次本机 MySQL 管理账号、端口和密码，点击“验证连接并保存”。已有受保护的 `/etc/training-registration/mysql-root.cnf` 时可自动复用其中的管理凭据。
2. 将业务源码、前端锁文件、数据库迁移入口与工程根 `deploy-console.json` 提交到远端目标分支。Git根没有描述时，会查找唯一的嵌套工程描述；多个工程都有描述时，须在Git根提供唯一选择，不能随意猜测。
3. 面板添加项目，填写 Git 与必要的凭据、获取并选择分支，保留推荐目录，勾选“保存后立即拉取代码并发布”。已有项目使用“发布项目”补接缺失服务。
4. 自动流程为：拉取代码 → 识别/补接服务 → 创建项目库和账号/写连接配置 → 构建后端 → 检查并执行迁移/基础初始化 → 启动后端 → 构建发布前端。
5. 发布记录显示各步骤及冲突原因。勾选持续自动更新后，成功的一端才开启计划。

MySQL 管理密码不是面板登录密码。面板不安装第二套 MySQL、不替换宝塔实例。凭据只存在服务器受保护配置中，不回显、不写 Git、不提供给工程代码。

## 2. 被部署工程示例：培训报名系统

以下是外部培训报名工程的接入示例，不是管理面板自身的部署描述或源码依赖。面板也支持Git根目录在业务工程上一级的嵌套布局，路径以描述文件所在工程为基准解析。必须将描述和业务代码一起提交到目标分支；只更新面板不能让旧业务 DLL 获得迁移功能。

```json
{
  "front": { "directory": "src/frontend", "output": "dist" },
  "back": {
    "project": "src/backend/TrainingRegistration.Api/TrainingRegistration.Api.csproj",
    "healthPath": "/health/ready"
  },
  "database": {
    "kind": "mysql",
    "connectionStringName": "TrainingRegistration",
    "planArguments": ["--deployment-database-plan"],
    "applyArguments": ["--deployment-database-apply"],
    "bootstrapArguments": ["--bootstrap-deployment-admin"]
  },
  "runtime": {
    "AllowedHosts": "*",
    "ReverseProxy__KnownProxies__0": "127.0.0.1",
    "CourseFiles__RootPath": "{data}/course-files"
  }
}
```

`runtime` 只补充缺失的非敏感默认值，保留服务器已有值。`{data}` 替换为项目可写目录 `/var/lib/deploy-projects/<slug>`。此业务示例默认允许独立站点的请求 Host，管理员可在服务器配置中收紧到实际域名。后端仍仅监听回环地址，会话、CSRF、首次改密和业务权限规则不变。

只有一端时将另一端设为 `null`；省略表示自动查找。已有服务不会因字段改成 null 而被删除。未声明 database 的工程继续使用原有数据库接入方式。

## 3. 前端规则

- 自动模式面向 npm 静态站点。源码目录需有 `package.json`、匹配并已提交的 `package-lock.json` 和 `scripts.build`。
- 固定在 `front.directory` 执行 `/usr/bin/npm ci`、`/usr/bin/npm run build`。只有 build:h5/build:prod 时，由开发者补正确的 build 别名。
- `directory` 相对描述文件所在工程目录，`output` 相对构建目录，例如 src/frontend + dist；产物应含静态入口 index.html。
- 未指定目录时在选中的工程依次检查 src/frontend、frontend、web、client、工程根；仍未找到且只识别到一个后端时，再检查其上级目录旁的前端。输出默认 dist，含 react-scripts 依赖时默认 build。
- 路径使用英文、数字、下划线、短横线、点和 `/`，不能用绝对路径、中文、空格或 `..`。产物不允许符号链接、硬链接或特殊文件。
- 生产环境由 Nginx 提供静态文件，不执行 npm run dev，不需前端启动脚本。支持 SPA 路由回退；同项目存在后端时自动代理 `/api/`。
- 前端 API 推荐 `/api/...`，不要写开发电脑的 localhost。构建变量随工程提供，不能放数据库密码；后端 runtime.env 不会注入前端构建。
- uni-app 只接入 H5 产物，output 按实际 H5 构建目录填写，如 dist/build/h5。小程序/App 包不能作为网页发布。
- pnpm/yarn、SSR、特殊 monorepo 使用管理员审核的自定义配方，不自动改变原工程工具链。

## 4. 后端和数据库迁移协议

自动模式识别 .NET Microsoft.NET.Sdk.Web 项目，多个 Web 项目需指定 back.project。在描述文件所在工程目录执行 dotnet publish，生成 `.deploy-output/back`，自动创建 `deploy-project-<slug>.service` 启动业务 DLL。程序集名取 AssemblyName 或项目文件名，也可指定 back.assembly（不带 .dll）。已接入的程序集入口改变会提示冲突。

服务器需安装工程要求的 .NET SDK/运行时、Node/npm。Java、Node 后端继续使用现有手工接入方式。healthPath 必须能从本机 HTTP 无登录访问并返回 2xx，不能依赖登录或 HTTPS 跳转；本项目使用 /health/ready 检查数据库与迁移就绪，不填时只有 TCP 检测。

声明 database 代表工程请求自动建库，并提供随业务 DLL 发布的离线命令。命令以非 root 项目运行账号执行，不启动 Web 服务，只获得本项目的迁移账号。

| 字段 | 含义 |
| --- | --- |
| kind | 自动模式固定 mysql，适配 MySQL 8；不把 MariaDB 当作已验证兼容。 |
| connectionStringName | 应用实际读取的连接名，面板写入 ConnectionStrings__名称。 |
| assembly | 可省略，必须为该后端程序集，不带 .dll。 |
| planArguments | 只读迁移计划参数。 |
| applyArguments | 执行计划的参数。 |
| bootstrapArguments | 可省略；首次基础数据初始化，必须支持安全重试。 |

本工程已实现这些入口。其他工程使用模板前需实现同一协议，不能只复制参数名称。仓库描述不接受 root 命令或数据库管理密码。

计划命令最后一行输出 JSON，退出码 0。字段：status=planned、applied（已应用迁移ID数组）、pending（待应用ID数组）、fingerprints（每个迁移ID对应64位SHA256摘要）、planToken（64位计划SHA256）、conflicts（冲突说明数组）。失败须非零退出且输出脱敏结果。

入口读取 `DeploymentDatabase__ExpectedDatabase` 和 `DeploymentDatabase__ExpectedServerUuid`，精确匹配目标；执行时读取 `DeploymentDatabase__PlanToken`，获得数据库锁后重新核对计划。成功返回 status=applied、空 pending/空 conflicts、相同的完整 fingerprints。基础初始化返回 initialized，重复初始化返回 alreadyInitialized，不能重置已有账号。

本项目使用既有 EF Core 迁移：历史必须是当前代码迁移的连续前缀；禁止倒退、跳过或重写已应用迁移。新空库自动执行全部初始迁移；已有库升级包含破坏性操作、删除索引/约束或需审核的原生 SQL 时会提示冲突。

## 5. 数据库生命周期和总关停

库名由项目标识稳定生成。面板创建两个随机密码的独立账号：运行账号只有该库增删改查权限，迁移账号的读写和结构变更权限也仅限该库。重试保留账号与密码，不重复创建。

| 情况 | 自动行为 |
| --- | --- |
| 首次发布 | 建库、建账号、写连接、迁移、必要基础数据初始化。 |
| 后续无迁移 | 验证版本与摘要，复用数据库，不重置基础数据。 |
| 后续有兼容迁移 | 自动生成 SQL 压缩备份，校验通过后升级，再启动后端。 |
| 同名库/账号无本项目所有权记录 | 提示冲突，不悄悄接管或覆盖。 |
| 已有运行连接指向不同库 | 提示冲突，不把现有业务库换成空库。 |
| 实例、历史或迁移摘要不匹配 | 停止本次发布，保留原数据与服务。 |
| DDL失败、进程中断、执行结果不确定 | 记录阶段并保留备份，不自动清表、重建或盲目重试。 |
| 应用回滚 | 只回退应用文件/服务，不回退数据库。 |

本项目迁移可能创建触发器。启用二进制日志且需要时，面板记录原值、临时调整 `log_bin_trust_function_creators`，并在结束或异常路径中恢复；若进程被强制终止，下一次准备/迁移先处理恢复标记，再提示未确认状态。不要让其他自动部署器并行修改同一数据库。

自动项目库的“总关停”先停前后端，再锁定本项目数据库账号，**宝塔共享 MySQL 实例继续运行**。卡片显示“访问已暂停”；总按钮变为“总开始”，点击后依次恢复数据库访问、后端、前端。数据库为“待初始化”时先执行后端发布中的迁移和初始化，再启动服务；迁移结果未确认时保留现场并提示核对发布记录。恢复不重新开启自动发布计划，需按需手动打开。已有 systemd/Docker 数据库仍按原规则启停整个实例。

卡片健康主要反映 TCP 与已记录阶段，不能代替业务读写验收。新库未初始化显示“待初始化”，迁移结果未确认显示“需处理冲突”。

## 6. 文件和服务器要求

| 文件/目录 | 用途 |
| --- | --- |
| 工程根 deploy-console.json | 工程结构、数据库协议、非敏感运行默认值；支持Git根下唯一嵌套描述。 |
| /etc/deploy-console/targets/项目标识.json | 自动生成的服务绑定，root:deploy-console、0640。 |
| /etc/deploy-console/projects/项目标识/runtime.env | 后端运行配置与项目运行账号连接串，root、0600。 |
| /etc/deploy-console/mysql.json | 面板授权的本机MySQL管理连接，root、0600。 |
| /var/lib/deploy-console-host/项目标识/database.json | 所有权、账号、迁移阶段与摘要；含秘密，仅root可读。 |
| /var/lib/deploy-console-host/项目标识/database-backups/ | 升级前备份，不提交Git。 |
| /var/lib/deploy-projects/项目标识 | 非root业务进程可写的持久目录。 |

默认前端 `/www/wwwroot/<slug>/current`、后端 `/opt/<slug>/current`、源码 `/opt/<slug>/repository`。首次接入的 current 不得预先存在，连空目录也不要创建；面板创建版本目录和链接。目录不能与其他项目重叠，受保护父目录须 root 所有且其他用户不可写，不能为通过检查递归修改共享目录权限。

后端发布目录只读，持久文件放项目数据目录下。端口由面板管理，不覆盖 ASPNETCORE_URLS。自动端口从 15000–25000（含边界） 中选择未占用端口。分配前探测IPv4/IPv6绑定，并避开已登记项目及其变更后的端口；已停止服务仍保留端口。已接入项目继续使用现有端口，端口全部占用时提示冲突，不越界分配。

宝塔默认生成 `/www/server/panel/vhost/nginx/deploy-console-<slug>.conf`；标准 Nginx 生成 `/etc/nginx/conf.d/deploy-console-<slug>.conf`。只管理独立站点。“面板设置 → 默认域名与HTTPS”配置已有ACME证书/私钥路径后，现有及后续Nginx前端自动使用HTTPS，沿用各自端口，无需单独去宝塔配置SSL；后端仍为本机HTTP，由前端代理`/api/`。页面访问应使用前端卡片的链接，例如`https://www.halfsuger.top:18081/`。未配置证书时保持HTTP。DNS、防火墙及云安全组按服务器实际入口配置，证书续期和文件要求见[HTTPS升级说明](deploy-console-https-upgrade.md)。

Git支持标准端口HTTPS与ssh://git@主机/路径，不能在地址中嵌密码。自建Git需进入允许主机列表。SSH密钥、子模块、LFS及私有依赖源按工程环境准备，不会自动复制开发电脑凭据。

## 7. 已有项目、自动更新和冲突处理

“发布项目”重新读取描述，自动管理的项目会补接缺失前端/后端、同步构建配方和健康检查，保留已有端口与本地运行值。人工接入服务不会被直接替换，已有数据库绑定不会被抢占。

绑定/数据库配方改变时，即使Git提交不变也会重建验证。定时任务使用已登记配方；改工程结构后用“保存并发布”或“发布项目”同步。停止一端只暂停该端计划；后端停止时跳过数据库准备，继续处理未停止的前端。启动不会自动开启计划。周期支持每分钟、5/15/30分钟、每小时、每天、永不。

| 提示/现象 | 处理 |
| --- | --- |
| 未授权MySQL | 面板设置中填写一次管理凭据后重试。 |
| 前端仍未接入 | 确认完整升级、远端所选分支有源码/锁文件/build脚本及描述，然后发布项目。 |
| 已有库/账号归属冲突 | 按提示名称核对所有权，不删除现有库来强行通过。 |
| 已有不同连接串 | 核对正在使用的业务库，不直接改成新空库。 |
| MySQL权限不足 | 管理账号需能建库、建账号、授予该库权限和备份；触发器迁移可能需要调整相关选项。 |
| 原生SQL或破坏性迁移冲突 | 提交兼容的前向迁移，或单独审核该变更，不伪造历史。 |
| 创建/迁移结果待核对 | 核对所有权记录、实际结构、迁移历史及备份，再做前向修复；不直接清除恢复标记。 |
| 旧自动部署timer仍运行 | 一个项目保持一个部署入口，不与面板同时发布。 |
| 首页正常但业务失败 | 检查HTTPS、可信代理、运行域名、必要依赖、数据库读写与后端就绪。 |

## 8. 交接与验收

同事填写[项目交接清单](deploy-console-onboarding-examples/project-handoff.md)，交付Git/分支、工具版本、源码/产物目录、健康接口、迁移协议和脚本、非敏感运行配置、持久目录与负责人，不填写真实密码。

模板：[普通前后端](deploy-console-onboarding-examples/deploy-console.fullstack.example.json)、[纯前端](deploy-console-onboarding-examples/deploy-console.frontend-only.example.json)、[纯后端](deploy-console-onboarding-examples/deploy-console.backend-only.example.json)、[MySQL自动部署](deploy-console-onboarding-examples/deploy-console.mysql.example.json)。手工绑定片段继续供非自动模式使用，不可覆盖整个target。

验收：首次自动建库 → 迁移/初始化 → 前后端健康 → 真实业务读写 → 重复发布不重复初始化 → 新版本自动更新 → 停止恢复 → 冲突提示。后续变更使用新的版本化迁移，不能改写已应用迁移。

本项目提供 `tests/scripts/verify_deploy_console_mysql.py`，在本机专用Docker MySQL创建独立验证库，检查初始化、幂等、目标匹配和最小权限；拒绝远端Docker或错误端口。先编译API再运行；保留隔离库与脱敏记录，不改业务库。Linux systemd、宝塔Nginx、真实Git和整套发布仍需服务器验收。

依据：provision.py、database.py、host.py、DeploymentDatabaseCommand，以及既有DeploymentBootstrapCommand。[完整运维说明](deploy-console.md)。

## 9. 宝塔MySQL客户端权限提示

保存管理凭据时若提示“接入文件或父目录权限不安全”，先检查元数据，不展示配置文件内容：

```bash
namei -l /usr/local/lib/deploy-console/database.py /run/deploy-console-db /etc/deploy-console /www/server/mysql/bin/mysql /usr/bin/mysql
```

2026-09-24实机回报：面板适配器、临时目录、配置目录权限正常，但 `/www/server/mysql/bin` 和 `mysql` 客户端属于UID 7161；`/usr/bin/mysql`只是指向该客户端的软链接。检查在执行客户端前拦截，尚未验证输入的数据库密码。

对于上述已核实的布局，只修正客户端目录本身和两个工具文件的所有者；保留原组和访问模式。`-h`禁止跟随最后一级软链接，命令没有递归，不改变数据目录、mysqld服务程序或MySQL服务账号：

```bash
chown -h root /www/server/mysql/bin /www/server/mysql/bin/mysql /www/server/mysql/bin/mysqldump
namei -l /www/server/mysql/bin/mysql /www/server/mysql/bin/mysqldump
```

确认目录和两个客户端均为root所有、非软链接且组/其他用户不可写（通常0755），再回面板保存密码。已安装的自动MySQL版可直接重试，无需重装面板或重启MySQL。若路径、所有者或类型与上述证据不同，先核对，不能用`chown -R`或`chmod -R`修改共享MySQL目录。

后续客户端选择已改为优先检查系统客户端，再检查宝塔客户端；某个候选不安全时尝试下一个，全部不满足时返回明确的客户端权限提示，仍不绕过root归属、父目录权限或软链接检查。
