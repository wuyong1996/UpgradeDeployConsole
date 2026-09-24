# OPS-01 本地验证记录

## 2026-09-24 最新部署包与旧交付自动清理

- 按用户明确要求新增`scripts/Package-DeployConsole.ps1`和归档组装校验助手。统一命令先恢复依赖、构建Vue、锁定NuGet恢复并发布Release，再校验归档每个文件/页面引用，复制校验通过后删除旧交付。并发锁、固定目录边界、软链接/目录联接拒绝及未知文件预检保护清理范围。打包临时目录结束后清除，不生成.sha256文件。
- `python -m unittest discover -s tests/scripts -p 'test_deploy_console_package.py' -v`：8/8通过，无跳过，实际执行临时目录归档和PowerShell清理；构建命令在流程测试中替换为隔离fixture。覆盖连续打包只留最新3文件、构建/校验失败保留旧包、同名版本拒绝、未知文件及链接保护、缺资源/敏感文件拒绝。升级流程6/6通过；PowerShell与Bash安装/升级脚本语法通过。本轮未重复运行无改动的业务功能测试，不复用历史通过数作为本次测试数。
- 已执行真实`Package-DeployConsole.ps1`：npm依赖安装/类型检查/Vite34模块构建、NuGet锁定恢复及.NET Release发布通过。生成`deploy-console-server-20260924-083535911.tar.gz`，55文件、613407字节，SHA256 `48bd56b38c6e572a04b5449cb6a6403812ce8f0431335b1e39bc8ad9456f8c34`。仅将摘要作为本地核验记录，不要求服务器读取校验文件。
- 清理38个旧交付条目并覆盖通用升级脚本；`deployment-artifacts`最终仅有本次tar.gz、upgrade-deploy-console.sh和DEPLOY.md，无旧压缩包、解压/发布目录、旧校验文件、旧升级脚本或历史接入附件。临时package目录已删除。新包与当前Release DLL、页面、运行适配器逐项核对一致，升级脚本默认包名正确，归档内外说明/脚本一致。
- 更新README、当前部署说明和AGENTS的后续打包规则；历史升级说明改为指向最新打包流程。本地现有5088健康，未重启未改动的应用、未连接服务器执行安装/清理、未删除服务器项目数据或恢复备份。未提交/推送Git；源码与本地密码/状态保留。

> 本文件原有记录来自拆分前工程。历史命令中的`src/operations/`现对应独立仓库的`src/`；涉及TrainingRegistration的记录属于被部署业务工程，不是本面板的构建依赖。本次拆分验证见[独立工程迁移说明](standalone-migration.md)。

## 2026-09-24 可选不备份删除

- 新增默认开启的“删除前备份”选项，取消时必须另行确认不生成恢复备份，并输入项目标识/确认删除数据。API与root适配器双端验证；任务、主机阶段记录和审计保存选择。旧请求/旧任务默认备份，预览返回备份选择及是否锁定；不备份结果不返回恢复目录，历史明确标注不备份。
- 不备份路径不调用tar备份/mysqldump、不创建恢复备份目录，保留共享服务和独立保存的删除恢复备份；仍执行全部停机/归属/路径/数据库连接检查。只记录重试所需元数据，不保存数据库密码。备份失败且停在preparing阶段可改选；进入清理后不可更改选择，结果不明的DDL拒绝重放。
- `dotnet test tests/DeployConsole.Tests/DeployConsole.Tests.csproj --no-restore --verbosity minimal`：49/49通过，无跳过，含默认兼容、独立确认、两种删除任务/审计/主机传参及幂等冲突。`python -m unittest discover -s tests/scripts -p 'test_deploy_console*.py' -q`：118/118通过，无跳过，其中删除专项32项；含无库/有库、不备份无归档、失败可改选、清理后锁定、不重复DDL、结果不明确保护、原有归属检查及真实HTTP错误契约。Linux命令/MySQL使用mock，文件使用隔离临时目录。
- `node --test tests/scripts/deploy-console-controls.test.mjs tests/scripts/deploy-console-form.test.mjs`：10/10通过。`npm run build --prefix src/operations/frontend`：类型检查及34模块构建通过，最终页面脚本`/assets/index-DQHhwI9m.js`。隔离HTTP夹具加载真实页面，浏览器验证默认备份、取消备份后按钮阻止未确认请求、切换选项清除旧确认；提交模拟请求收到backupBeforeDelete=false/confirmWithoutBackup=true，历史明确显示不生成本次恢复备份，无真实项目删除。
- Release发布完成；Debug构建0警告0错误，本地5088面板用受管脚本停止/启动并恢复健康。首次调用Windows PowerShell5.1不符合脚本7.4要求，已改为当前PowerShell直接执行；首次并发构建因Vue替换静态文件导致.NET资产过时，已按前端→后端顺序重建；测试中的区域比较警告已修正。最终命令均通过，不影响业务应用服务。
- 升级包版本为20260924-delete-options，通用脚本默认指向新版；继续不读取.sha256，缺包跳过，健康后核对并清理解压目录。升级流程测试新增此版本，旧版具名脚本/已交付压缩包保留原样。Bash语法与diff检查通过。
- 未连接服务器或执行真实删库、生产升级/删除、恢复演练；需用户升级后核对新选项。未提交/推送，保留用户无关修改。

- 最终交付核验：54文件、617836字节，SHA256 `cd6a30e0b9fcc7fcaa313c96fa0c92c5bf4315cbeeb4eadb2d247f7c7224cf9d`；归档逐文件与暂存目录一致，5088健康且HTTP脚本与包内字节一致。旧delete-legacy包摘要未变，两种新版脚本与源码一致且为LF。包内VALIDATION为打包前快照，不含自身摘要。

## 2026-09-24 缺包跳过、不读取校验文件及遗留解压目录清理

- 用户明确要求不存在文件跳过、解压目录也清理、不要再读取.sha256。新增通用`deploy/console/upgrade.sh`：包存在则安装，缺包提示跳过安装；健康检查后，对生成目录中的DLL、页面及主机模块与已安装内容作对应检查，清理本次、旧版随机目录及直接解压的同名目录。旧校验文件只可选删除，不读取、不依赖；缺包跳过不宣称已升级。
- 清理范围限定/root内的指定包名及已知生成目录。软链接、realpath变化、其他版本、解压外层额外文件、内容不一致及挂载点保留；本次安装结果不能对应时保留本次包和目录，前置安装/健康失败不清理。当前运行版本、项目资源和恢复备份不在删除范围。
- `python -m unittest discover -s tests/scripts -p 'test_deploy_console_upgrade_instructions.py' -v`：6/6通过，无跳过，覆盖25个流程场景。Bash执行真实文件存在/路径/内容比较；安装、HTTP、Linux挂载检测和rm由测试替代，rm仅记录参数。覆盖缺包、缺校验文件、无效校验内容不读取、仅有遗留解压目录、失败保留、其他文件/不同内容/软链接/挂载点保护和清理失败保留包。
- 两种下载名`upgrade-deploy-console.sh`、`upgrade-deploy-console-20260924-delete-legacy.sh`均与源码一致且为LF；Bash语法、嵌入Python AST及git diff检查通过。原delete-legacy压缩包摘要仍为`f67dbb35876995a4eea5e4b2f4e3a65ad5012058e3bf2101b355c8e4550cdff5`，未重打包。同步4份当前升级说明及主文档，旧归档内脚本不会自行改变，需用户覆盖服务器上的旧升级脚本。
- 本轮未连接服务器或执行实际清理。仅改升级入口与文档/测试，无API、页面或运行适配器变更，不编译或重启应用，不以先前测试结果当作本轮已执行；没有Git提交/推送。

## 2026-09-24 升级成功后清理上传材料

- 用户要求每次升级后删除升级包。4份升级说明统一为校验、独立临时目录解压、安装、HTTP与JSON status=healthy确认、临时目录精确边界/realpath/非软链接核验、删除本次目录与两个精确上传文件。采用set -euo pipefail，任一步前置检查失败不会继续删除；不清理已安装版本、项目数据、恢复备份或历史上传目录。
- 生成`deployment-artifacts/upgrade-deploy-console-20260924-delete-legacy.sh`便于对已交付包执行新流程；它与当前说明首个Bash块相同，增加root预检。完整delete-legacy归档未重打包，SHA256仍为`f67dbb35876995a4eea5e4b2f4e3a65ad5012058e3bf2101b355c8e4550cdff5`。旧归档内说明保持历史内容，用户应使用新脚本或仓库当前说明。
- `python -m unittest discover -s tests/scripts -p 'test_deploy_console_upgrade_instructions.py' -v`：4/4通过，无跳过，覆盖4份真实说明的36个流程场景。Bash实际执行流程、管道和路径判断；校验/解压/安装/HTTP/删除命令被隔离替代，rm仅记录参数，不递归删除真实目录，不调用服务器。验证各前置失败不清理、成功只传入本次精确路径、目录清理失败不删除压缩包。
- Git Bash对独立脚本的`bash -n`及`git diff --check`通过。应用、安装器和主机适配器未改，不重新编译或重启服务；未连接生产服务器、未清理用户实际上传文件、无Git提交/推送。服务器实际安装与清理需按更新说明执行。

## 2026-09-24 旧版自动项目删除兼容与处理提示

- 用户服务器只读输出确认项目级autoManaged缺失、front/back均为true、database.autoProvisioned为true。对照本地20260923-autopublish已交付provision.py确认旧版没有生成项目级及前端标记；本次修复旧格式识别，不要求用户重建、发布或手工补标记。兼容仍要求自动后端标记、确定的生成服务/专属账号和运行目录，并保留完整删除前核验；预览不改写配置。
- 新增7项回归：旧格式无项目/前端标记及后续补接前端的格式可识别、明确false不能覆盖、缺少生成服务证据拒绝、Nginx手改/数据库连接仍阻止、已移除服务的清理可重试且不重复删库、全部标记缺失不能推定为旧版自动接入。`python -m unittest discover -s tests/scripts -p 'test_deploy_console_deletion.py' -q`：24/24通过；全量`test_deploy_console*.py`：104/104通过，无跳过。文件备份/清理用临时目录，Linux服务和MySQL行为使用mock。
- `npm run build --prefix src/operations/frontend`：vue-tsc/Vite生产构建通过，34模块，脚本`/assets/index-OVgqJulV.js`。隔离HTTP夹具加载真实新页面，浏览器确认拒绝时显示“删除前需要做什么”、目标接入文件路径及四项条件，重新核对成功后显示资源清单、备份位置及原确认输入；未提交实际删除，临时页面已关闭。
- .NET业务代码/API契约未修改。`dotnet build src/operations/DeployConsole.Api/DeployConsole.Api.csproj --no-restore --verbosity minimal`：0警告0错误；Release发布至`deployment-artifacts/deploy-console-upload-20260924-delete-legacy/published`通过。受管5088面板停止后按原脚本重启成功；不影响其他本地业务服务。本轮未重复.NET/Node测试，不将上轮结果计为本轮执行。
- 用户服务器仅由用户执行管理标记的只读命令；本轮未连接服务器、修改生产配置、删除数据库/文件或执行恢复。真实Linux归属检查、删库和恢复仍需升级后验收。没有Git提交/推送。
- Python语法、Git Bash安装器`bash -n`及`git diff --check`通过。交付`deploy-console-server-20260924-delete-legacy.tar.gz`，53文件、607938字节，SHA256 `f67dbb35876995a4eea5e4b2f4e3a65ad5012058e3bf2101b355c8e4550cdff5`；逐文件与暂存目录一致，本地5088健康且提供的JS与包内新脚本字节一致。初版delete包SHA256未变。归档内VALIDATION为打包前快照，不包含自身摘要。

## 2026-09-24 项目信息与确认删除

- 按用户要求显示数据库名称/地址/项目账号/迁移状态与数量、Git源码及构建缓存目录、后端systemd名称与开机启动状态。总关停后提供删除预览，输入项目标识并勾选确认后进入异步删除；成功移除项目及Git凭据，保留带项目名的历史、审计和恢复备份。
- root适配器从自动接入配置推导范围，重新检查停止标记、实际进程、目录归属、挂载点/软链接、其他项目重叠、MySQL实例与专用账号。管理账号需直接拥有PROCESS权限，避免活动连接查询权限不足产生假阴性。备份及完整性校验通过才清理；失败锁住项目，重试不重复删库，结果不明确的MySQL DDL要求人工核对。
- `dotnet test tests/DeployConsole.Tests/DeployConsole.Tests.csproj --no-restore --verbosity minimal`：46/46通过，无跳过；包含确认条件、版本/幂等、失败锁定、成功后项目和凭据移除及审计保留。`dotnet build src/operations/DeployConsole.Api/DeployConsole.Api.csproj --no-restore --verbosity minimal`：0警告0错误。
- `python -m unittest discover -s tests/scripts -p 'test_deploy_console*.py' -q`：97/97通过，无跳过，其中删除专项17项。临时目录实际执行tar/gzip备份、读取/摘要、清理和符号链接边界验证；Linux所有权、systemd/Nginx和MySQL命令使用mock。HTTP回归使用隔离Kestrel验证删除预览状态冲突、非法指纹及缺少CSRF。`node --test tests/scripts/deploy-console-form.test.mjs tests/scripts/deploy-console-controls.test.mjs`：10/10通过。
- `npm run build --prefix src/operations/frontend`：vue-tsc和Vite生产构建通过，34模块，新脚本`/assets/index-C4rDFPcT.js`。隔离HTTP夹具加载真实构建页面，浏览器验证新增信息、总关停后删除入口、资源和备份预览、错误项目标识不能提交、正确标识及勾选后提交、项目移除及带项目名的历史。该界面测试只操作内存模拟项目，不调用主机删除器；窄窗口确认弹窗可滚动。随后修正删除记录沿用回退说明的问题并重新构建。
- Python语法、安装器`bash -n`和`git diff --check`通过。没有连接正式服务器、执行实际MySQL删除、Linux服务清理或恢复演练，没有Git提交/推送；生产操作需在完整升级后由用户预览并确认。Linux测试服务器删除/恢复验收及真实目录权限仍待核对，见[升级说明](deploy-console-delete-upgrade.md)。
- `dotnet publish src/operations/DeployConsole.Api/DeployConsole.Api.csproj -c Release -p:UseAppHost=false -o deployment-artifacts/deploy-console-upload-20260924-delete/published --no-restore --verbosity minimal`通过。受管面板已停止后按`scripts/Start-DeployConsole.ps1`重新启动，5088 `/health/live`返回`healthy`；未重启无关业务服务。
- 交付`deploy-console-server-20260924-delete.tar.gz`：53文件、604198字节，SHA256 `0c2907591fa900eab2e2786bebd89ea8f614538914f1506d9239de88537c58b9`。归档逐文件与暂存目录一致，包含deletion.py/升级和恢复说明，不含运行状态、密码或证书私钥；本地HTTP页面与包内脚本逐字节一致且含新增信息与删除文案，旧ACME交付包摘要保持不变。归档内VALIDATION为打包前验证快照，本条摘要仅记在仓库文档，避免自引用摘要。

## 2026-09-24 ACME通配符证书路径兼容

- 用户提供的ACME目录与私钥文件实际含`*`，上一版.NET/Python路径规则未允许该字符，保存会被拒绝；`.cer`扩展名本身没有限制。新增精确截图路径回归，修复前Python用例明确失败，修复双端规则后通过；不展开路径、不执行shell、不改变默认域名规则或权限检查。
- `dotnet test tests/DeployConsole.Tests/DeployConsole.Tests.csproj --no-restore --verbosity minimal`：43/43通过，无跳过，包含API编译。`python -m unittest discover -s tests/scripts -p 'test_deploy_console*.py' -q`：80/80通过，无跳过，包含证书校验、TLS握手、Nginx回退及HTTP鉴权回归。
- 前端代码没有变化，复用上一轮已构建并验证的页面；脚本名仍为`/assets/index-B6tw9X-O.js`，仅凭页面脚本不能辨别本次API及Python修复。升级必须使用完整修正版，见[HTTPS升级说明](deploy-console-https-upgrade.md)。
- 未读取服务器证书或私钥内容，未连接服务器、未修改生产文件/权限、未提交或推送Git。实际证书内容、有效期、域名覆盖和权限由服务器保存时验证，截图文件名不作为这些条件已经通过的证据。
- Release发布命令`dotnet publish src/operations/DeployConsole.Api/DeployConsole.Api.csproj -c Release -p:UseAppHost=false -o deployment-artifacts/deploy-console-upload-20260924-https-acme/published --no-restore --verbosity minimal`通过；受管本地管理面板停止/重启后5088健康。修正版`deploy-console-server-20260924-https-acme.tar.gz`共51文件、587186字节，SHA256 `ab7e68ac5f7e40170177cb9154fd98ea3ad4046cc97369ba18008975061e0f54`，归档逐文件一致，Python语法核验通过；页面产物逐字节与上版相同，旧HTTPS包摘要保持不变。

## 2026-09-24 默认域名与ACME证书HTTPS

- 新增面板域名/完整证书链/私钥路径设置、受认证及CSRF保护的GET/PUT接口、root证书校验和受保护快照、已有Nginx前端批量应用及失败回退。原前端端口保留，卡片显示HTTPS访问地址，后端标记本机接口端口；已停止站点保持停止。
- 新增systemd证书检查timer，每15分钟检查文件变化。有效更新才重载，错误保留原快照并记录脱敏同步错误；ACME签发/续期仍由服务器原任务负责。
- `python -m unittest discover -s tests/scripts -p 'test_deploy_console_tls.py' -q`：14/14通过。包含本机OpenSSL真实证书链/域名/有效期/密钥匹配，以及Python真实TLS握手/SNI/系统风格信任检查；测试CA仅用于隔离测试，不修改系统信任。Nginx应用/回退及timer触发由mock验证，未在Windows执行Linux Nginx。
- `python -m unittest discover -s tests/scripts -p 'test_deploy_console*.py' -q`：79/79通过，无跳过。HTTP回归使用隔离Kestrel，验证HTTPS接口未认证401、缺CSRF403、非法输入400、Windows主机不支持409。
- `dotnet test tests/DeployConsole.Tests/DeployConsole.Tests.csproj --no-restore --verbosity minimal`：41/41通过。`node --test tests/scripts/deploy-console-form.test.mjs tests/scripts/deploy-console-controls.test.mjs`：10/10通过。
- `npm run build --prefix src/operations/frontend`：vue-tsc及Vite生产构建通过，29模块，新脚本`/assets/index-B6tw9X-O.js`。`dotnet publish src/operations/DeployConsole.Api/DeployConsole.Api.csproj -c Release -p:UseAppHost=false -o deployment-artifacts/deploy-console-upload-20260924-https/published --no-restore --verbosity minimal`通过。
- `python -m py_compile deploy/console/host.py deploy/console/tls.py`、Git Bash `bash -n deploy/console/install.sh`及`git diff --check`通过。`scripts/Start-DeployConsole.ps1 -Stop`后重新启动，本地5088管理面板健康；未重启无关业务服务。
- 用户尚未提供实际服务器证书路径；没有连接正式服务器、应用生产Nginx配置或运行生产迁移，没有Git提交/推送。Linux安装器/systemd timer实际运行、服务器证书链、公网HTTPS及业务登录待安装后核对，见[升级说明](deploy-console-https-upgrade.md)。
- 交付`deploy-console-server-20260924-https.tar.gz`：51文件、587041字节，SHA256 `f53a9c8cf6445aa5b7ed5e9548c4c89d66bc76a9cd785ea9949059d29d6262d8`；归档内每个文件与暂存目录逐字节一致。包含tls.py、新service/timer及完整升级说明，不含运行密码、状态或证书私钥。本地HTTP获取的新JS与包内JS字节一致且含设置文案，`/health/live`返回healthy；旧交付包保持原样。

## 2026-09-24 自动端口15000–25000

- `provision.py`将新接入前后端分配范围改为15000–25000（含两端），范围用尽明确报错。探测前排除target中的服务/数据库端口，以及主机state记录的变更端口；已停止项目不释放保留端口。
- 通过IPv4/IPv6通配地址bind检查占用，能发现仅绑定指定地址、IPv6或已bind未listen的TCP端口。探测套接字及时关闭，探测异常失败关闭；沿用现有全局操作锁控制面板内分配顺序。Windows下需SO_EXCLUSIVEADDRUSE才能得到排他绑定语义；首轮真实套接字回归发现差异，修正后通过。
- `python -m unittest discover -s tests/scripts -p 'test_deploy_console_provision.py' -q`：18/18通过。`python -m unittest discover -s tests/scripts -p 'test_deploy_console*.py' -q`：65/65通过，无跳过；覆盖边界、端口用尽、已登记/改动/停止端口保留、IPv4绑定、IPv6监听及探测异常。
- 新包`deploy-console-server-20260924-ports.tar.gz`逐文件归档核验通过：46文件、564440字节，SHA256 `9a2a246c72841aede417a20d78364efac1c6b6a2b51c95d398dc175826989202`。发布DLL/页面与上版已验证产物逐字节一致，Python文件语法核验并规范为LF；旧包保持原样。
- `python -m py_compile deploy/console/provision.py`、`git diff --check`通过；既有换行提示保留。仅修改Python及文档/测试，无.NET/Vue代码变更，复用上版已验证发布产物，没有重复编译未改动项目。
- `pwsh -NoProfile -File scripts/Start-DeployConsole.ps1`重启本地5088管理面板并健康通过。未执行生产升级、业务发布或数据库迁移；Linux systemd/Nginx/MySQL场景仍需服务器核对，新版交付见[端口范围升级说明](deploy-console-port-range-upgrade.md)。

## 2026-09-24 总关停后的总开始

- 根因：总关停后的发布入口受停止状态保护，缺少项目级恢复操作；待初始化数据库可能使直接启动后端健康检查失败，而原发布操作又被停止状态拦截。
- 增加start-all，恢复数据库访问后对待初始化后端/缺失版本执行既有发布，再恢复后端和前端；失败不执行后续步骤，不擅自重放结果未确认的迁移。全停时总按钮切为总开始，恢复中允许再次总关停，成功后自动发布计划继续暂停。
- 停止优先：入队时间传递至适配器，防止较新的停止已完成但恢复进程刚启动时误清除；短时意图锁保护检查与清除标记；后台状态以项目版本阻止较晚返回的恢复结果覆盖新停止。
- `npm run build --prefix src/operations/frontend`通过vue-tsc及Vite构建，27模块，新脚本`index-IZvUOBMn.js`。
- `dotnet test tests/DeployConsole.Tests/DeployConsole.Tests.csproj --no-restore --verbosity minimal`：33/33通过。第一次与前端构建并行，静态资源在编译期间被Vite替换导致资产缺失；前端完成后顺序重跑通过。最后内部请求时间字段调整后再次33/33通过。
- `python -m unittest discover -s tests/scripts -p 'test_deploy_console*.py' -q`：59/59通过，包括新增恢复顺序、待初始化强制发布、缺少版本、数据库/健康失败、后续停止优先和不确定迁移拒绝重放。服务/MySQL由mock替代，HTTP测试使用隔离本地Kestrel。
- `node --test tests/scripts/deploy-console-form.test.mjs tests/scripts/deploy-console-controls.test.mjs`：10/10通过，包含全停切换、部分运行、状态过期与仅单端项目。
- `python -m py_compile deploy/console/host.py`、`git diff --check`通过；差异检查有原有CRLF提示。`dotnet publish src/operations/DeployConsole.Api/DeployConsole.Api.csproj -c Release -p:UseAppHost=false -o deployment-artifacts/deploy-console-upload-20260924-start-all/published --no-restore --verbosity minimal`通过。
- 新包`deploy-console-server-20260924-start-all.tar.gz`：36文件、561467字节；SHA256 `d7db517d290269c8799275cacb20e0a251e347835a7d936b6c4940e88414b4ff`，归档逐文件与暂存目录核对一致。Python/安装脚本归一为LF；本地HTTP页面与包内页面均引用`/assets/index-IZvUOBMn.js`，HTTP获取的脚本含总开始文案。旧mysql包SHA256仍为`ecdae60aedf89ae556a86eeddb41fafc4ee381e7e464a398d1c6b81b4efb3d54`，与原校验文件一致。
- `pwsh -NoProfile -File scripts/Start-DeployConsole.ps1`重启本地管理面板并验证5088健康。没有重启业务服务，没有执行真实生产SQL，没有Git提交或推送；真实Linux/MySQL恢复待服务器安装新版后核对。旧mysql升级包保留，新包与[升级说明](deploy-console-start-all-upgrade.md)单独交付。

## 2026-09-24 项目数据库接入代码提交与远端核对

- 用户确认授权提交并推送限定的5个文件；未将其他未提交页面、控制台、文档或配置改动混入提交。
- 提交前重新核对目标`github.com/wuyong1996/pdeProject`、本地master及远端master均为`0efaa6cb35ff019c22568d3366f16992f401c81e`，5文件SHA256与前一轮审阅及验证内容一致，暂存区原为空。`git diff --cached --check`通过。
- 提交`cefa0f989d2fcaec67a9881cb86855ed35dfbc2f`：`feat(OPS-01): enable automatic project database deployment`，恰为5文件/209行新增；使用限定路径提交。`git push origin HEAD:refs/heads/master`成功，随后`git ls-remote --heads origin refs/heads/master`确认远端为同一提交；暂存区为空，其他工作区修改保留。
- 本轮未修改已验证的接入实现，因此没有重复编译或重复数据库测试。未登录服务器代为点击发布或执行SQL；面板需一次“发布项目”读取新描述并登记数据库绑定，不把Git推送成功等同于服务器发布成功。

## 2026-09-24 宝塔MySQL客户端归属排查与兼容修复

- 服务器反馈保存MySQL密码时出现“接入文件或父目录权限不安全”。用户提供`namei -l`：适配器为root:root 0755，临时目录root:root 0700，配置目录root:deploy-console 0750；`/www/server/mysql/bin`和`mysql`为7161:mysql 0755，`/usr/bin/mysql`为指向宝塔客户端的软链接。由代码定位为执行客户端前的所有权检查拦截，不能据此判定密码正确或错误。
- 针对已核实布局交付`chown -h root`精确处理bin目录本身、mysql和mysqldump三个路径；不递归、保留组和访问模式、不改数据目录或服务运行账号。命令由用户在服务器执行；尚未把待执行步骤当作修复成功。检查mysqldump后若类型/权限不满足要求须继续核对。
- `database.binary`现优先检查系统客户端，其次宝塔客户端；候选不可信或不可执行时继续寻找合格候选，全无可用项时给出明确的客户端权限或缺失提示。固定名称仅限mysql/mysqldump，不经PATH搜索，不绕过原安全检查。
- `python -m unittest discover -s tests/scripts -p 'test_deploy_console*.py' -v`：49/49通过，新增5项覆盖系统客户端优先、软链接候选被拒绝后继续、安全检查全部失败时不执行、执行权限及缺失/非法名称。MySQL和服务调用仍为mock；真实HTTP检查使用隔离Kestrel。
- `python -m py_compile deploy/console/database.py`及`git diff --check`通过（既有换行提示保留）；`pwsh -NoProfile -File scripts/Start-DeployConsole.ps1`成功，本地5088健康。只修改Python和文档/回归，不重复编译未改动的.NET/Vue项目。
- 原`deploy-console-server-20260924-mysql.tar.gz`及其SHA256保持不变，避免使已交付安装命令失效；后续客户端选择改进当前仅在工作区。服务器现有版本在精确修正客户端归属后即可重试，不要求再次升级面板。未提交或推送Git。
- 用户执行精确修复并重新保存后，明确反馈页面返回“连接验证通过，后续项目可自动建库和迁移。回到项目点击‘发布项目’即可重试。”据此确认服务器的MySQL连接配置已成功；这不代表项目已完成建库、迁移或前后端发布，后续发布记录仍需另行验收。

## 2026-09-24 前端补接与MySQL自动初始化

- 用户确认复用宝塔已安装的MySQL，自动建库、建账号和迁移。新增面板一次管理授权、项目独立账号/连接、数据库迁移协议、升级备份和阶段记录；复用现有EF迁移及必要管理员初始化入口，没有改写已应用迁移或现有账号规则。
- 根因核对：本机Git根为工程上级目录，旧版递归找到后端但只在Git根附近找前端。新版识别唯一嵌套`deploy-console.json`，或在唯一后端附近找前端；描述路径以工程目录为基准。已有自动管理项目重新发布会补接缺失服务，保留后端端口和运行配置。
- `dotnet build src/backend/TrainingRegistration.Api/TrainingRegistration.Api.csproj --no-restore --verbosity minimal`：0警告、0错误。
- `dotnet test tests/DeployConsole.Tests/DeployConsole.Tests.csproj --no-restore --verbosity minimal`：28/28通过。
- `dotnet test tests/TrainingRegistration.UnitTests/TrainingRegistration.UnitTests.csproj --no-restore --filter FullyQualifiedName~DeploymentMigrationPolicyTests --verbosity minimal`：3/3通过，覆盖历史连续前缀、需人工处理的迁移及现有迁移SQL摘要的离线确定性。
- `python -m unittest discover -s tests/scripts -p 'test_deploy_console*.py' -v`：44/44通过（主适配器17、数据库14、真实HTTP1、自动接入12）。覆盖同名资源/连接冲突、独立账号权限、重复发布、部分DDL保留现场、升级备份顺序、MySQL设置验证失败保留旧值、凭据不回显、二进制日志选项恢复、停止优先、嵌套仓库识别及缺失前端补接。MySQL、systemd、Nginx等调用为mock，不能当作服务器实测。
- HTTP检查使用真实隔离Kestrel，新增MySQL设置接口401/403/400/409及秘密不进入响应/状态文件；不向用户的项目状态写测试记录。`node --test tests/scripts/deploy-console-form.test.mjs`：6/6通过。合计81项定向检查通过。
- `npm run build --prefix src/operations/frontend`：类型检查、26模块生产构建通过。Python编译、安装器`bash -n`及`git diff --check`通过；保留已有换行转换提示。没有新增依赖。
- `dotnet publish src/operations/DeployConsole.Api/DeployConsole.Api.csproj -c Release -p:UseAppHost=false -o deployment-artifacts/deploy-console-upload-20260924-mysql/published --no-restore --verbosity minimal`：通过。完整升级包包含新增数据库适配器和离线运行包装器，不能只更新网页文件。
- `scripts/Start-DeployConsole.ps1`受管重启成功，5088 `/health/live`返回healthy。修改业务API后按约定通过`Start-Backend.ps1 -Stop`和`Start-Backend.ps1 -TimeoutSeconds 45`重启；进程曾启动，但数据库不可用导致就绪检查超时并停止，不能声称业务后端已恢复。日志：`.local/runtime/logs/backend-3446cd11-3672-4502-842b-6cb5460f2e63.log`。
- `python tests/scripts/verify_deploy_console_mysql.py`尝试真实本地验证，但在Docker容器检查阶段失败，尚未创建测试库或执行SQL。本机Docker引擎启动日志提示旧`sailor-ingest.sock`无法访问；启动既有Docker服务未解决，未重置Docker或删除数据卷。提供的版本化验证脚本仅接受指定本地Docker MySQL/回环端口，保留隔离验证库和脱敏记录，待Docker恢复后执行。
- 本轮未登录生产服务器、未运行生产建库/迁移/安装，未提交或推送Git；也未完成新版数据库设置页面的浏览器操作验证。Linux权限隔离、实际MySQL8初始化/升级/恢复访问、宝塔Nginx和真实Git完整发布仍需测试服务器验收。
- 交付：`deployment-artifacts/deploy-console-server-20260924-mysql.tar.gz`及展开目录中的`UPGRADE.md`、`project-integration.patch`。补丁仅包含本次4个业务接入文件；面板所选远端分支须同步这些文件。包内`VALIDATION.md`记录本轮验证，`PACKAGE-CHECKS.json`保存归档前内容核对结果。

## 2026-09-24 工程接入文档与模板

- 对照 `provision.py`、`host.py`、`register.py`、API发布编排和服务卡片实现整理接入规范。确认首次接入的数据库为 `none`，已有target直接复用；缺失前端不能仅靠修改仓库描述重新识别。
- 用Python解析全部7份JSON模板；在独立临时源码目录调用实际 `provision.discover`，前后端、纯前端、纯后端3种描述均匹配预期，检查构建命令、程序集和HTTP健康模式。
- 将4份服务器绑定片段分别合并到既有合法target样例，调用实际 `host.validate_target` 全部通过；另验证“后端18080 + 前端18081 + external数据库”的组合。这里只校验配置，不检查真实端口或执行服务命令。
- 文档内3段JSON可解析、2段Python检查脚本通过AST语法校验、11处本地链接有效；新增指南与模板无行尾空白、冲突标记或未闭合代码块。`git diff --check -- TASKS.md`通过（仅既有换行转换提示）。
- 验证使用 `python -` 内联脚本；输入夹具及汇总保存在 `.local/onboarding-doc-check-3ced2b73aa934a9f9a1f20e6259f1256/`，汇总文件为 `validation.json`。Linux专属导入使用现有测试模块的Windows替身，未调用其服务测试、未运行systemd/Nginx/Git命令。
- 可转交资料包为 `deployment-artifacts/deploy-console-onboarding-20260924.zip`，12个文件、26152字节；ZIP CRC、包内JSON、全部Markdown链接及源文件逐字节一致性检查通过。SHA256：`62f94d8ca9abbbe3fa157a810f2243872e51df4d9dd713bace94ede65dbe39d5`。包内不包含程序安装文件或实际凭据。
- 本轮只修改文档与配置模板，未执行项目编译、服务重启、远端发布、迁移或数据库连接。示例中的端口、unit、路径、健康接口与连接配置需按接入工程现场核对，不能将上述静态检查视为Linux实机接入验收。

## 2026-09-23 保存后自动接入与发布

- `dotnet test tests/DeployConsole.Tests/DeployConsole.Tests.csproj --no-restore --verbosity minimal`：28/28通过，包含API编译。新增保存/入队原子与幂等、无接入文件的首次任务、后端→前端顺序、成功启用计划、后端失败取消前端、保留已停止端、首次接入中总关停及显式重试。
- `python -m unittest discover -s tests/scripts -p 'test_deploy_console*.py' -v`：26/26通过（适配器16、自动接入9、真实HTTP1）。覆盖实际临时目录中生成接入文件/服务、仓库识别与描述文件、多个后端消歧、锁文件/非法路径/链接、占用端口跳过、宝塔重载命令、首次发布失败重试与用户停止优先。systemd/Nginx/Git克隆等系统操作为mock，不能算作Linux实机发布通过。
- 真实Kestrel HTTP检查使用独立临时状态和明确不可用的helper入口，保存自动发布立即返回201、同操作重放仅一个prepare任务、环境不可用时任务失败但项目仍保存；既有401/403/409等接口回归通过。该检查在编译结束后独立重跑通过。
- `node --test tests/scripts/deploy-console-form.test.mjs`：6/6通过。合计60项定向检查通过。
- `npm run build --prefix src/operations/frontend`：类型检查、24模块生产构建通过；Python编译、安装脚本Bash语法检查通过。首次编译发现CA1861及测试取消令牌规则，修正后通过；曾在HTTP进程运行期间遇到DLL锁的一次编译重试，最终编译/测试成功，后续HTTP验证改为顺序执行。
- `scripts/Start-DeployConsole.ps1`已重启本地面板，5088健康检查通过。浏览器表单验证在另外的临时状态与回环端口执行，不向用户本地或正式项目写入测试记录。
- 浏览器真实流程：获取公开仓库13个分支，默认master；新表单两个发布选项默认勾选，点击“保存并发布”后立即进入发布记录，仅生成一个排队任务；Windows执行入口返回“当前为非Linux环境，服务操作未执行”，页面显示失败与重试入口，项目仍保留。失败记录默认展开，不把排队或Windows预览显示为部署成功。隔离服务验证完成后停止，测试数据保留在独立.local目录。
- 本轮没有连接正式服务器。Linux systemd、实际私有Git、宝塔站点与数据库连接仍需实机验收；自动识别范围为npm静态前端及.NET Web，其他技术栈保留自定义接入方式，数据库安装和迁移不在自动接入范围内。
- Release发布命令`dotnet publish src/operations/DeployConsole.Api/DeployConsole.Api.csproj -c Release -p:UseAppHost=false -o deployment-artifacts/deploy-console-upload-20260923-autopublish/published`通过；完整包为`deployment-artifacts/deploy-console-server-20260923-autopublish.tar.gz`，包含provision.py与自动接入策略样例。未提交或推送Git，根目录部署描述尚需用户随业务源码发布后才会出现在远端。

## 2026-09-23 Git远端分支与认证

- `dotnet test tests/DeployConsole.Tests/DeployConsole.Tests.csproj --no-restore --verbosity minimal`：22/22通过，包含API编译；覆盖默认分支解析、加密保存及重启读取、凭据不回显、仓库与账号绑定、查询证明过期/来源变化、凭据移除后暂停计划、幂等冲突和主机白名单。
- `python -m unittest discover -s tests/scripts -p test_deploy_console.py -v`：15/15通过；新增Git凭据绑定、失败时临时文件清理、凭据不进入命令行和Git环境注入检查。真实systemd调用仍使用mock。
- `python -m unittest discover -s tests/scripts -p test_deploy_console_http.py -v`：1/1通过；真实隔离Kestrel新增验证分支接口401/403及非法地址/不允许主机400，既有项目接口回归通过。
- `node --test tests/scripts/deploy-console-form.test.mjs`：6/6通过；`npm run build --prefix src/operations/frontend`类型检查和22模块生产构建通过。合计44项定向检查通过。
- Python语法检查与Git Bash `bash -n deploy/console/install.sh`通过。
- 浏览器实际查询公开仓库`https://github.com/creeperyang/pinyin.git`：返回13个真实分支，默认选中`master`，可切换`dev`；查询过程中修改URL后，旧结果不会恢复，分支与保存按钮保持禁用；切换HTTPS认证显示用户名和密码/Token字段，未填写时不能查询。测试表单取消退出，没有新增项目记录。
- 本机直连GitHub 443失败，页面明确显示查询失败；复用本机已有Git代理作为受管面板进程的`HTTPS_PROXY`后查询成功，没有修改全局Git或服务器网络配置。
- 发布包：`deployment-artifacts/deploy-console-server-20260923-git.tar.gz`，包含更新后的API、页面、安装器和`git-auth.py`；升级须更新完整包，不能只覆盖页面。升级说明随包提供。
- `dotnet publish src/operations/DeployConsole.Api/DeployConsole.Api.csproj -c Release -p:UseAppHost=false -o deployment-artifacts/deploy-console-upload-20260923-git/published`通过；检查归档包含新页面与Git辅助脚本，不含运行密码、状态和密钥。`scripts/Start-DeployConsole.ps1`受管重启后健康检查返回healthy，重新登录与新增表单显示正常；`git diff --check`通过（仅既有换行转换提示）。
- 未执行：真实私有仓库账号验证、Linux `LoadCredential`/SSH/自动发布实机验证、正式服务器升级；这些仍需在服务器验收。宝塔业务站点服务控制适配不在本轮范围内。

## 2026-09-23 添加项目弹窗与标识生成

- `node --test tests/scripts/deploy-console-form.test.mjs`（Node 24.20.0）：6/6通过，覆盖中文首字母、英文/混合/数字开头、同名避重、长度边界、不支持字符回退和手填目录保护。
- `npm run build --prefix src/operations/frontend`：类型检查及22模块生产构建通过。首次类型检查发现模板对普通布尔变量推断为字面量的问题，改用Vue `ref`后通过；发布包使用修复后的构建。
- `dotnet build src/operations/DeployConsole.Api/DeployConsole.Api.csproj --no-restore`：0警告、0错误；仅验证面板项目，未改后端实现。
- `pwsh -NoProfile -File scripts/Start-DeployConsole.ps1`：本地受管面板重启，5088健康探针通过。
- 浏览器实际核对：输入名称时不提前生成；失焦后“培训报名系统”→`pxbmxt`并联动三个默认目录；填写Git地址后点击遮罩，弹窗及所有内容保留；手填前端目录后改名仍保留该目录；手填标识后改名不覆盖；键盘清空标识后再次使名称失焦可重新生成；取消和关闭按钮均正常。只使用未保存的临时表单，未创建项目记录。浏览器错误/警告为空。
- `dotnet publish src/operations/DeployConsole.Api/DeployConsole.Api.csproj -c Release -p:UseAppHost=false -o deployment-artifacts/deploy-console-upload-20260923/published`：通过，新版服务器包包含页面和新增依赖许可证。
- 本轮没有连接或修改正式服务器；服务器需使用新版包升级后才会生效。宝塔项目服务控制适配仍不在本轮范围内。

## 2026-09-22 原始实现验证

日期：2026-09-22。环境：Windows、本仓库、本地回环端口；不连接正式服务器。

| 验证 | 实际结果 |
| --- | --- |
| `dotnet test tests/DeployConsole.Tests/DeployConsole.Tests.csproj --no-restore --verbosity minimal` | 11/11 通过，包含 API 项目编译；密码变更/缺失、CSRF、独立暂停、永不、每日/每小时调度、总关停预检、幂等、版本冲突及恢复启动不自动恢复计划。 |
| `python -m unittest discover -s tests/scripts -p test_deploy_console.py -v` | 12/12 通过；接入白名单/路径、局部关停、部分总关停失败、外部数据库前置拦截、发布/端口失败恢复、未更新不构建、子进程输出上限与超时。服务控制命令被 mock。 |
| `python -m unittest discover -s tests/scripts -p test_deploy_console_http.py -v` | 1/1 通过；启动真实 Kestrel 和临时独立状态，验证 401/403/404/409/503、登录 Cookie、项目创建/幂等/过期写入、计划修改、未接入服务拒绝操作、OpenAPI、密码文件变更失效。 |
| `npm run build`（`src/operations/frontend`） | `vue-tsc --noEmit` 和 Vite 构建通过；18 个模块。 |
| Python 编译与 Git Bash `bash -n deploy/console/install.sh` | 通过。 |
| `scripts/Start-DeployConsole.ps1` | 仅启动/重启独立面板；`http://127.0.0.1:5088/health/live` 返回 healthy。 |
| 服务器交付包 | `npm run build --prefix src/operations/frontend` 和 `dotnet publish src/operations/DeployConsole.Api/DeployConsole.Api.csproj -c Release -p:UseAppHost=false -o deployment-artifacts/deploy-console-upload/published` 通过；确认包含 DLL、运行时配置和 wwwroot。安装器补充 ASP.NET Core Runtime 10 检查、独立用户组以及目录 0755/文件 0644 权限归一化，Bash/Python 语法检查通过，Linux 安装仍待实机验证。 |

浏览器实际交互核对：

- `admin` 登录，空项目列表及 Windows 未接入提示。
- 添加项目默认 `project` 与三个推荐目录；修改标识自动联动未改路径，手填前端目录保留。
- 保存临时项目后，服务页显示实际保存的前后端路径；未接入的启停、总关停、检查更新不可操作。
- 前端选“每小时”、后端选“每天”，各自独立保存；前端改“永不”时，后端仍为“每天”。
- Git 与目录页展示保存内容；总关停位于检查更新左侧。
- 桌面三列服务卡片与窄屏单列布局均已查看；浏览器错误/警告记录为空。
- 临时 UI 项目在核对后清理，只保留验证审计；恢复空项目列表供用户添加真实项目。

未执行：真实 Linux systemd/Nginx 发布、SSH 私有仓库、Docker 数据库关停、正式服务器安装、生产数据库迁移。当前 Docker Linux daemon 未运行，不能把 mock 测试当作这些操作通过的证据。Linux 安装顺序及验收要求见 [部署说明](deploy-console.md)。

既有培训业务文件的未提交修改保留；本任务没有修改业务前后端实现、数据库或现有自动发布脚本，没有提交或推送 Git。
