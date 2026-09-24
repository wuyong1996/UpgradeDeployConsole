# 通用服务器更新管理

OPS-01：独立 .NET 10 API + Vue 3 页面，面向一台 Linux 服务器上的多个项目。面板的运行、密码和配置不依赖被管理项目的数据库，没有绑定培训业务系统。

交给其他工程同事使用：[工程接入规范与排查指南](deploy-console-project-onboarding.md)。2026-09-24版增加自动补接缺失前端，以及复用宝塔MySQL自动建库、建账号、写运行连接、迁移和基础初始化；需要升级完整面板与适配器，并同步业务迁移入口到Git目标分支。

## 保存后自动部署

新版添加/编辑表单默认勾选“保存后立即拉取代码并发布”（已总关停或两端已停止的项目除外）。保存与入队在同一次状态写入中完成，重复请求不会重复创建项目或发布任务；页面自动进入发布记录。取消勾选可以只保存配置，兼容旧客户端不携带`deployAfterSave`的行为。

发布按“拉取代码 → 识别/补接服务 → 准备自动数据库 → 构建后端并迁移 → 发布后端 → 发布前端”执行。自动接入项目重新读取描述，补齐缺失前端并同步构建配方，保留既有端口与本地运行配置；无描述的人工接入项目沿用既有配置。后端失败取消尚未执行的前端发布，项目仍保留；解决冲突后点“发布项目”重试。手动停止的一端保持停止，后端停止时跳过数据库准备。首次接入中总关停可取消流程，之后用“恢复并发布”显式重试。

新项目默认勾选“发布成功后按检查周期持续自动更新”；只有发布成功的一端才开启计划，默认周期沿用每分钟，永不仍不排自动任务。失败的一端保持关闭。编辑现有项目时默认沿用其是否有开启的计划；仅保存配置不会新增发布任务。运行环境是Windows时可以保存，但后台记录明确显示未执行Linux服务操作，不能当作部署成功。

自动识别范围：带`package-lock.json`与`build`脚本的npm静态前端（优先`src/frontend`、`frontend`、`web`、`client`、仓库根目录），输出默认`dist`，CRA默认`build`；后端识别单个`Microsoft.NET.Sdk.Web`项目。多项目或特殊目录在仓库根放置如下`deploy-console.json`；某端设null表示首次不部署该端，已有服务不会自动删除。文件支持front/back、可选database迁移协议与非敏感runtime默认值，不接受root命令或服务名。Java、Node SSR等继续使用服务器接入文件。

```json
{
  "front": { "directory": "src/frontend", "output": "dist" },
  "back": { "project": "src/backend/Project.Api/Project.Api.csproj", "healthPath": "/health/ready" }
}
```

后端程序集默认取csproj的`AssemblyName`或文件名，可用`back.assembly`指定固定名称。指定健康地址时检查HTTP；未指定时仅检查进程与TCP端口，不能代表业务数据库已就绪。本仓库已提供带`/health/ready`的部署描述，需随业务代码提交后远端才能读取。

自动接入由root配置`/etc/deploy-console/auto-deploy.json`控制，安装器首次生成默认允许GitHub/GitLab/Gitee的配置，升级保留旧策略；自建Git须同时加入面板查询白名单与此策略。自动分配15000–25000（含边界）范围内未占用、未登记的前后端独立端口。分配前通过IPv4/IPv6通配地址绑定探测，避开已登记前后端、数据库以及主机状态中记录的变更端口（含已停止服务）。同次前后端分配和不同项目自动接入由既有全局操作锁串行处理；范围用尽则明确报错，已有项目继续使用原端口。外部进程仍可能在检测后抢占端口，最终启动失败会按既有发布流程报告。首次接入要求前后端的`current`路径尚不存在，即使是已创建的空目录也会拒绝；已有源码目录必须归专用构建账号所有，拒绝覆盖已有站点和其他项目目录。构建以独立非root账号执行；仓库内容只在隔离构建和非root业务服务中执行。

后端运行配置位于`/etc/deploy-console/projects/<slug>/runtime.env`，合并缺失的runtime默认值并保留本地设置。声明database后自动写入项目库运行连接，已有不同连接会提示冲突；未声明时沿用原方式。面板设置一次授权本机MySQL，管理凭据仅存root配置，运行/迁移账号分离。迁移先检查归属和历史，已有库升级前备份；失败或结果不确定保留现场，不自动重建或回退数据库。培训工程复用既有管理员初始化命令，不重置已有账号。运行数据目录`/var/lib/deploy-projects/<slug>`可写，发布目录只读；服务单元`deploy-project-<slug>.service`仅首次健康发布后启用开机启动。

支持标准Nginx以及宝塔默认路径：检测`/www/server/nginx/sbin/nginx`时使用`/www/server/panel/vhost/nginx/deploy-console-<slug>.conf`并调用该Nginx检查与重载；否则使用`/usr/sbin/nginx`与`/etc/nginx/conf.d`。只生成独立站点文件，不覆盖其他站点。服务器仍需预装正确版本的Node/npm、.NET SDK及已启动的Nginx。

在“面板设置 → 默认域名与HTTPS”填写域名、服务器现有ACME完整证书链及私钥的绝对路径，保存后自动为已有和后续Nginx前端配置HTTPS，无需到宝塔操作SSL。沿用前端端口，例如`https://www.halfsuger.top:18081/`，服务卡片提供访问链接；后端端口保持本机HTTP。未配置证书时沿用原HTTP行为。证书检查系统信任、域名、有效期、密钥匹配和文件权限，错误不会替换已验证的配置；已停止的站点保持503。

安装器增加`deploy-console-https.timer`，每15分钟检测源文件变化，验证成功才更新root保护的快照并重载Nginx；ACME申请及续期仍由服务器原任务负责。面板显示到期、最近应用/检查时间与同步错误。DNS、防火墙和云安全组需允许实际前端端口；管理面板自身HTTPS入口保持原配置。文件要求、升级和排查见[HTTPS升级说明](deploy-console-https-upgrade.md)。

添加项目时点击弹窗外部不会关闭；关闭按钮和取消按钮仍可主动退出。名称失去焦点后自动推荐项目标识：中文取拼音首字母（例如“培训报名系统”→`pxbmxt`），英文规范为小写及短横线，最长24字符，数字开头补`p-`，已占用标识追加序号；已有项目和服务器接入标识均参与避重。无法转换时回退到`project`及序号。自动推荐使用本地`tiny-pinyin 1.3.2`（MIT），不发送名称到外部服务；多音字采用库的默认读音，标识仍可手动修改。手填标识不会被后续名称失焦覆盖，清空后再次使名称失焦可重新生成；编辑已有项目不会改动标识。推荐目录随标识联动，手填路径保留。

## 本地启动与构建

```powershell
npm ci --prefix src/frontend
npm run build --prefix src/frontend
dotnet build src/DeployConsole.Api/DeployConsole.Api.csproj
pwsh -File scripts/Start-DeployConsole.ps1
```

访问 `http://127.0.0.1:5088`，首次密码 `admin`。本地文件 `.local/deploy-console/password.txt`，状态 `.local/deploy-console/data/state.json`，接入文件 `.local/deploy-console/targets`。再次启动脚本会核对 PID 与启动时间，只重启它管理的面板。`-Status` 查询，`-Stop` 停止；均不影响已有业务服务。

Windows 可验证真实登录、项目配置、计划保存、审计和接口；系统服务控制仅支持 Linux，未接入不会生成模拟健康状态。2026-09-24增加业务API离线迁移入口；仅启动面板不会执行业务数据库操作。

前端沿用现有 Vue/Vite/TypeScript 版本，使用独立锁文件及依赖目录，不改变业务前端包版本。构建输出直接进入 `src/DeployConsole.Api/wwwroot`，再执行 `dotnet publish` 会携带页面。

## Linux 安装顺序

适用单机 systemd + 标准或宝塔默认路径Nginx，已有 ASP.NET Core Runtime 10（`dotnet --list-runtimes` 中须有 `Microsoft.AspNetCore.App 10.x`）、Python 3.10+、sudo、Git。构建账号需另有项目所需的 Node/npm、.NET SDK 或 Java 工具链；自动模板使用`/usr/bin/npm`和`/usr/bin/dotnet`，其他位置需先由服务器管理员配置工具入口或使用手工接入文件。不自动安装软件、不自动开防火墙、不设置域名。

1. 在构建机器生成最新完整面板包（核验成功后自动删除旧交付）：

   ```sh
   pwsh -NoProfile -File scripts/Package-DeployConsole.ps1
   ```

2. 推荐上传`deployment-artifacts`中的最新包与升级脚本，按生成的`DEPLOY.md`执行。若管理员已手工解压，可将包内`published`路径传给同包`console/install.sh`；以下为直接安装器示例：

   ```sh
   sudo bash deploy/console/install.sh /absolute/path/to/published-console
   ```

   安装独立 `deploy-console.service`，仅监听 `127.0.0.1:5088`。发布目录统一为 root 持有、目录 0755、文件 0644，保证独立运行账号可读取从不同机器上传的包；不要将运行凭据放进发布包。状态目录仅面板账号可写。保留已有密码和状态；不启动或停止任何业务项目。升级时重复本步骤，安装器保留旧面板版本以便人工回退链接。

   使用服务器压缩升级包时，按[当前升级命令](deploy-console-delete-upgrade.md)使用新版`upgrade.sh`解压、安装及健康检查；不读取或要求.sha256，缺包提示跳过安装。健康检查后清理本次包、可选残留校验文件及能核对为已安装内容的同版解压目录，失败保留。`install.sh`接收的是发布目录，不掌握原压缩包位置，因此清理由外层升级脚本完成；仅运行安装器不会自行搜索或删除上传文件。此规则适用于后续升级交付，旧包内历史说明需改用新脚本。

3. 按 `nginx.example.conf` 配置独立 HTTPS 域名和证书，先 `nginx -t`，再 reload。不要把管理面板挂在受管理项目的站点下。正式环境的 Cookie 必须为 Secure，不能用公开 HTTP 登录。初始 `admin` 按用户约定提供；由服务器管理员编辑密码文件。面板不提供读取密码接口。

   ```sh
   sudoedit /etc/deploy-console/password.txt
   # UTF-8 单行文本；root:deploy-console，0640；目录 0750。
   ```

   密码每次请求读取，变更后会话立即失效；删除、空文件或权限错误时拒绝登录，不回退到 admin。升级不会覆盖密码。浏览器会话最多 8 小时，写接口有 CSRF 验证；登录每源 IP 每分钟最多 5 次。未信任转发客户端 IP，反向代理后的登录限流为所有面板用户共享。

## 自定义项目手工接入（可选）

常见项目可使用上面的自动接入。以下方式适用于自定义构建命令、非自动识别框架或需要绑定已有服务的项目：由root维护同名接入文件，绑定目录、服务身份、允许Git主机及构建命令，之后日常操作可在页面完成。保存时未勾选发布则只保存配置。

1. 复制 `deploy/console/project.example.json` 到 `/root/project.json`，编辑项目真实信息；保证文件与父目录均 root 持有，不可被其他用户写入。
2. 前端可以为 `nginx` 静态站点或独立 `systemd` 进程；后端为 `systemd`。前后端可为 `null`（未配置）；至少一端存在。示例命令是通用示范，`Project.Api.csproj`、构建目录、输出目录和健康路径必须替换，不能直接用于当前培训项目。
3. `independentDeploy: true` 表示两端可独立发布、接口和数据库前后兼容。人工接入不自动接管已有数据库，自动建库/迁移见工程接入规范；不能把不兼容变更直接交给自动发布。
4. 为后端预先安装 systemd 单元，使用独立的非 root 业务账号，`WorkingDirectory` 指向对应 `current`，`ExecStart` 为实际启动命令。端口必须由 `ASPNETCORE_URLS`、`SERVER_PORT` 或 `PORT` 环境变量控制，并与接入文件初始端口一致。不要在启动参数中覆盖该变量；否则改端口会健康检查失败并恢复旧设置。后台服务只应监听本机；Nginx 会按新后端端口同步代理 `/api/`。
5. 运行接入注册，不启动业务服务：

   ```sh
   sudo python3 -I deploy/console/register.py /root/project.json
   ```

   创建专用 `deploy-build-project` 账号与源码工作目录，准备 root 管理的版本目录，写入 `/etc/deploy-console/targets/project.json`。注册会拒绝已有实体 `current` 目录、其他项目重叠目录或重复服务身份，不会移动或覆盖现有部署。
6. 私有 HTTPS Git 仓库：在页面选择“用户名 + 密码 / Token”，填写独立认证字段。GitHub需要Personal Access Token，其他Git服务依其支持填写密码或Token。SSH仍由独立构建账号设置只读deploy key和已验证的known_hosts。禁止关闭主机校验，禁止把凭据填进Git URL。仅支持标准端口的`https://host/path.git`和`ssh://git@host/path.git`；发布时地址主机仍必须在接入文件`allowedGitHosts`中。
7. 页面添加相同标识，填写Git URL及认证信息，点击“获取分支”后从远端下拉列表选择，优先选仓库默认分支；不再手填固定main。没有分支、查询失败或信息已变化时不能保存。默认推荐：前端`/www/wwwroot/project/current`，后端`/opt/project/current`，源码`/opt/project/repository`。已接入配置的路径必须匹配。检查远端成功后可分别“立即发布”；首次发布成功后再开启对应自动计划。

### 分支查询与Git凭据

- HTTPS分支查询可以在注册接入文件之前执行，Windows本地也可真实查询。默认允许github.com、gitlab.com、gitee.com，以及对应项目root接入文件中的主机。其他自建Git主机由管理员在systemd服务配置中添加`Environment="Console__AllowedGitHosts__0=git.company.example"`后daemon-reload并重启面板；不要使用通配符扩大主机范围。
- SSH查询必须先完成Linux接入，复用该项目构建账号的密钥；页面不上传SSH私钥。Git查询关闭交互提示和HTTPS重定向，启用TLS校验，不读取系统/用户Git配置中的credential helper。需要代理的查询进程可由管理员通过服务环境变量`HTTPS_PROXY`配置；不能依靠桌面用户的全局Git配置自动生效。
- 仓库、账号、认证方式、凭据或项目标识变化会清空旧分支列表，异步旧响应不会覆盖新输入。查询凭证有效10分钟，绑定项目、仓库和认证内容；过期或重启后重新获取。空仓库需要先提交代码。
- 保存才会写入凭据。`state.json`仅持有ASP.NET Core Data Protection加密密文，密钥持久化于`/var/lib/deploy-console/git-keys`，Linux目录0700；凭据与完整仓库URL、账号绑定，不能转用于另一个仓库。更新请求摘要使用服务端HMAC，不存可离线猜测的裸密码摘要。恢复面板时需一起保留状态与密钥目录。
- 项目接口只返回`gitAuthMode`和`gitUsername`，不回显密码、Token或密文。编辑可使用服务器已保存凭据、更换凭据或改为无需认证；更换仓库/账号后必须重新提供认证。变更认证配置会暂停两端自动计划。
- 后续检查和自动发布通过受限helper的stdin传递解密后的凭据；仅Git网络步骤用systemd `LoadCredential`临时装载，在进程环境中设置限于该仓库的认证头，命令行、Git URL及Git配置文件不含秘密。临时文件位于root-only `/run/deploy-console-git`，正常结束或失败均清理；构建命令不装载这些凭据。需systemd支持`LoadCredential`；真实Linux私有仓库发布仍需实机验收。

对既有项目：先在维护窗口停用它原来的自动发布 timer/任务，避免两个发布器同时修改服务。当前版本采用独立的版本目录，不自动迁移现有站点；管理员需要先完成迁移与服务身份核对再接入。现有 `training-auto-deploy` 未被本任务更改或停用。

## 操作语义

| 操作 | 行为 |
| --- | --- |
| 停止前端 / 后端 | 立即持久化对应暂停意图，取消该端排队任务、中止该端构建；关停服务。另一端的计划、下次时间和版本保持不变。 |
| 前端 Nginx 停止 | 仅对应站点返回 503；不停止共享 Nginx 进程。端口仍监听维护响应。 |
| 总关停 | 按前端、后端、数据库逐项停止；各步骤失败仍尝试其他项并记录部分失败；前后端计划全部暂停。面板自身继续运行。 |
| 总开始 | 项目总关停或全部已接入服务已停止时，原按钮切换为“总开始”。按数据库访问恢复 → 后端 → 前端执行；待初始化数据库先通过后端发布完成迁移，缺少当前版本时自动发布。任一步失败停止后续步骤，已恢复的步骤不会自动回退为停止。成功后解除停止标记，自动发布计划仍暂停，需手动开启。新的关停请求优先；恢复中可再次总关停。 |
| 删除项目 | 总关停且无进行中任务后显示。先预览确切资源、输入项目标识并确认删除数据，默认备份，也可另行确认不备份，再清理本面板自动创建的前后端、源码、运行目录及独立MySQL项目库/账号。共享服务、其他项目、恢复备份和历史记录保留；归属或停止状态有冲突时拒绝。详细流程见[删除升级说明](deploy-console-delete-upgrade.md)。 |
| 外部数据库 | 仅 TCP 连通性检测，不能声称掌握真实进程状态；未接管时总关停在动作前整体拒绝。 |
| 自动MySQL项目库 | kind=mysql；总关停锁定本项目账号访问，恢复时解锁，不停止宝塔共享实例。卡片区分待初始化、冲突和访问暂停。 |
| 数据库共享 | 同一身份在其他已登记项目使用时页面展示影响范围。停库会影响这些项目。不会删除库、卷、数据，不会改数据库端口。 |
| 启动 | 恢复指定服务，不自动恢复其发布计划；总关停后使用“总开始”统一恢复。 |
| 周期 | 每分钟、5 分钟、15 分钟、30 分钟、每小时、每天、永不；按保存时间起的间隔运行，每天是每 24 小时。前后端分别显示完整下次时间，Asia/Shanghai。 |
| 永不 | 不排自动任务，允许手动检查与发布。 |
| Git / 分支切换 | 保存后两端自动计划暂停；检查更新返回远端分支列表。之后手动发布或显式重新开启计划。 |
| 修改端口 | 检查占用、更新限定的配置、重载代理和健康检查；失败尝试恢复旧端口。已停止的服务保持停止。 |
| 发布 | 查询目标 SHA、按已确认 SHA checkout、隔离构建、不可变版本目录、原子 current 链接切换、健康检查；失败恢复上一应用版本。没有上一版本则停止失败服务。 |
| 回退 | 回到该端保存的上一应用版本；不是任意指定历史提交；不回退数据库。 |

任务异步执行，页面展示排队、阶段、结果和历史。操作标识提供幂等性；配置版本拒绝过期写入。停止请求会抢先处理，但 systemd 停止、Nginx reload、文件切换等短临界步骤须完成，不能承诺毫秒级停止。构建命令有独立时限；整个适配器最长 50 分钟；失败的自动计划至少等待 10 分钟或原周期再试，避免连续重启风暴。

重启面板不会重新开启暂停的计划；未完成任务会标为中断，用户应核对实际状态。服务状态来自现场探测；项目草稿、Windows、断连不会显示为健康。数据库健康表示 TCP 连接通过，不等于业务 SQL 验证。

## 安全与运行边界

- API 不以 root 运行；sudoers 仅允许无参数执行固定 root-owned `host.py`，JSON 从标准输入传递。服务器端再次校验项目、路径、目标和来源。
- `host.py`只信任root-owned且父目录不可被其他用户写入的接入文件/策略，使用固定模板创建服务、数据库和账号。浏览器不能提交Shell命令、服务名或数据库身份。仓库迁移入口仅以非root账号运行，通过systemd LoadCredential获得本项目库的迁移凭据。
- 构建使用独立非特权账号，在 systemd 临时单元中运行，设置只读系统、私有临时目录、只允许源码工作目录写入、禁止访问面板密码和状态、无提权能力；命令结束终止整个构建进程组。
- 发布产物拒绝符号链接、硬链接与特殊文件，复制后清除危险权限；current 和发布目录由 root 控制。运行服务账号没有发布目录写权限。
- 前端 CSP、同源请求及 CSRF；会话只在内存中，Cookie HttpOnly / SameSite Strict。错误与操作记录不输出原始命令、环境变量、Git 凭据或 stderr。
- 面板 JSON 状态原子替换并持有独占锁，仅支持单实例；请备份状态与 root 主机状态 `/var/lib/deploy-console-host`。尚无多节点、分布式锁和大规模日志归档。
- 日常发布保留发布版本、构建工作目录和证书快照，不按时间自动清理，需管理员设置磁盘监控及保留策略。明确确认“删除项目”后会清理该项目的发布版本和源码，恢复备份仍保留。当前实现支持已有证书的HTTPS配置与同步，不自动签发证书；不提供Windows服务管理、自动迁移已有部署和多服务器代理。
- 生产接入、发布及数据库变更由管理员按本项目流程进行；本次开发未连接任何正式服务器。

## API 与验证

登录后可读取 `/openapi/v1.json`。API 前缀 `/api/v1`：

- `POST /auth/login`、`GET /auth/session`、`POST /auth/logout`；其余接口需要会话。
- `GET/POST /projects`、`PUT /projects/{id}`；写入带 `version`、`Idempotency-Key` 与 `X-CSRF-Token`。
- `POST /git/branches`：请求`{repository, slug, projectId?, gitAuth:{mode, username, secret?, useStored}}`；响应`{branches:[{name,commit}], defaultBranch, verification}`。需会话及CSRF，最多2个并发查询，HTTPS查询30秒超时，最多5000个有效分支。错误返回400/409/422/429/503/504且不包含Git原始stderr。
- 项目写入可携带上述`gitAuth`和`branchVerification`，以及可选布尔`deployAfterSave`、`enableSchedule`（API默认false，页面主动传入）。保存与prepare任务入队原子完成，响应仍为项目对象，进度由jobs读取。新版页面必须使用查询凭证并选择已查询分支；旧客户端保持原契约。
- `GET /targets`、`GET /projects/{id}/status`；返回受限接入摘要及现场状态。
- `PUT /projects/{id}/plans/{front|back}`：`version, autoDeploy, periodSeconds`。
- `POST /projects/{id}/jobs`：`version, action, side, port?`；`action: "publish"`启动或重试完整项目发布流程，内部先prepare后依次deploy；202后轮询`GET /jobs?projectId=&page=`。无接入文件但正在prepare时，总关停可以取消首次接入。
- 同一jobs接口新增`action: "start-all"`，不传side；要求已有服务绑定、数据库受管或无数据库、无进行中的项目任务。按幂等键和项目版本防重复/并发覆盖；恢复任务不重新启用自动发布。未完成接入的项目仍使用“恢复并发布”。数据库迁移处于未确认阶段时提示核对发布记录，不自动重置迁移状态。
- `GET /projects/{id}/deletion`：已总关停、无活动任务时预览删除范围，响应`fingerprint, resources:[{kind,name}], backupDirectory, resume, backupBeforeDelete, backupModeLocked`。同一jobs接口接受`action: "delete", confirmationSlug, deletionFingerprint`及原有版本/幂等键/CSRF，side为空。新增可选`backupBeforeDelete`（默认true）和`confirmWithoutBackup`（默认false）；选择false必须另行明确确认。选项持久化至任务/审计并传递给主机。预览的`backupModeLocked`表示已进入清理、重试不可切换；不备份结果的`backupDirectory`为null。主机执行前重新验证范围、进程与归属；成功后移除项目及Git凭据，保留操作历史中的项目名和备份位置。失败保留项目，`deletionRequested=true`时拒绝启动、发布、编辑和自动计划，允许重试删除。
- 项目服务状态新增可空`databaseName, host, username, migrationState, appliedMigrations`；这些是项目库身份与部署记录，不含密码。页面额外显示已有`repositoryPath`、后端服务名和开机启动状态。
- `GET /audit?page=`、`GET /settings`；历史每页 20 条，按新到旧排序。
- `GET/PUT /settings/mysql`：读配置摘要或提交`{username, port, password}`验证本机MySQL并保存root配置。需要会话，PUT需要CSRF；密码不回显、不进入面板状态或审计，失败返回400/409。`GET /targets`新增数据库kind=mysql表示自动创建的项目库。
- `GET/PUT /settings/https`：读取HTTPS配置摘要或提交`{domain, certificatePath, privateKeyPath}`验证并应用。需要会话，PUT需要CSRF；返回`configured, domain, certificatePath, privateKeyPath, expiresAt, appliedAt, lastCheckedAt, syncError`，不返回证书/私钥内容及内部快照路径，失败返回400/409。服务状态新增可空`accessUrl`，仅配置HTTPS的Nginx前端提供。

```powershell
dotnet test tests/DeployConsole.Tests/DeployConsole.Tests.csproj
python -m unittest discover -s tests/scripts -p test_deploy_console.py -v
python -m unittest discover -s tests/scripts -p test_deploy_console_http.py -v
npm run build --prefix src/frontend
```

适配器单元测试 mock 子进程，不会关停真实服务。上线前仍需在一台 Linux 测试机完成真实 systemd/Nginx/私有 Git/端口变更/构建失败回退及共享数据库总关停演练。Windows 上的本地预览、编译和 mock 测试不替代这些验收。
