# 独立工程迁移

2026-09-24，按用户要求从原工程抽离至`D:/UpgradeDeployConsole`。这是迁移，不是只复制一份源码；原面板目录已移出。

| 原位置 | 新仓库位置 |
| --- | --- |
| src/operations/DeployConsole.Api | src/DeployConsole.Api |
| src/operations/frontend | src/frontend |
| deploy/console、tests/DeployConsole.Tests | 同名目录 |
| tests/scripts中的面板测试 | tests/scripts |
| docs/deploy-console*、scripts/Start-DeployConsole.ps1 | 同名路径 |
| deployment-artifacts中的面板交付文件 | deployment-artifacts（Git忽略） |
| .local中的面板状态及验证材料 | .local（Git忽略） |

独立补齐SDK、中央NuGet版本、构建属性、solution、编辑/换行配置和Git忽略；保留原依赖版本与锁文件。测试中的原培训工程源码依赖改为本仓库接入模板的临时示例。无需复制培训后端、前端或数据库才能构建面板。

原培训工程保留自己的`deploy-console.json`、迁移CLI实现和业务验证脚本；MySQL真实业务验证脚本改名为`tests/scripts/verify_training_deployment_mysql.py`以明确归属。它不属于面板测试套件。历史交付包内容保持原样，不能把旧包内的目录文字当作当前源码路径。

本地运行状态整体迁移，敏感内容未输出；凭据文件、密钥和历史包均由Git忽略。迁移清单保存在本机`.local/extraction-moves.json`，不提交Git。

## 本次验证

- 已将67个面板源码/目录/交付项迁至新根目录，旧测试缓存另外归档到新仓库忽略的`.local/`；原`src/operations`、`deploy/console`、面板测试及启动脚本不再留有第二份。旧.NET构建缓存已移入`.local/pre-extraction-build-cache`，从新目录重新恢复依赖，未复用旧路径的编译缓存。
- `npm ci --prefix src/frontend`：安装47包，锁文件依赖恢复成功；`dotnet restore UpgradeDeployConsole.slnx --locked-mode`：2项目恢复成功，原NuGet版本/锁文件保持不变。
- `npm run build --prefix src/frontend`：类型检查及34模块构建通过；`dotnet build UpgradeDeployConsole.slnx --no-restore --verbosity minimal`：0警告0错误。所有编译输出路径位于新根目录。
- `dotnet test tests/DeployConsole.Tests/DeployConsole.Tests.csproj --no-restore --no-build --verbosity minimal`：49/49通过；`python -m unittest discover -s tests/scripts -p "test_deploy_console*.py" -q`：118/118通过；Node两个测试文件10/10通过。共177项，无跳过。Bash安装/升级语法及Python/XML/JSON静态解析通过。
- `scripts/Start-DeployConsole.ps1`在新目录启动，5088健康。使用迁移后的原密码完成真实本机登录、读取项目和设置、无网络请求的非法Git输入校验及退出；密码文件、项目集合和加密Git凭据保持一致。首次PowerShell检查把空API数组当作单项，已用Python按JSON数组及项目ID重新验证，并非数据变化。本机项目数原本为0；不涉及服务器上的项目。
- 独立Git根为`D:/UpgradeDeployConsole`，未设置远端、未暂存/提交/推送。92个源码及文档文件可纳入Git，`.local`、密码、密钥、历史交付包、node_modules和构建输出均被忽略；检查无私钥/PAT特征，无执行代码对旧工程绝对路径或`src/operations`的引用。Markdown相对链接全部可解析。
- 最近delete-options与delete-legacy交付包SHA256与迁移前一致。历史验证记录保留原文，不冒充本次新验证；原培训业务文件与无关未提交改动保持。服务器安装、Linux/MySQL真实发布/删除未执行，也未重新打包旧服务器归档。

