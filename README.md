# 服务器更新管理系统

独立的多项目自动部署面板：管理Git来源/分支、前后端服务/端口/计划、MySQL自动建库及迁移、已有ACME证书HTTPS、项目总启停与确认删除（可选择不备份）。

## 工程目录

```text
src/DeployConsole.Api/   .NET 10 API和调度服务
src/frontend/            Vue 3 + TypeScript页面
deploy/console/         Linux安装、升级与主机适配脚本
scripts/                 本地受管启动脚本
tests/                   .NET / Python / Node测试
docs/                    部署、接入规范、示例与验证记录
UpgradeDeployConsole.slnx
Directory.Build.props / Directory.Packages.props / global.json
```

`.local/`保留本机密码、项目/凭据、日志等运行状态；`deployment-artifacts/`只保留最新部署包、升级脚本和部署说明，均由Git忽略。源码可放在任意目录，运行不依赖原培训报名工程。

## 本地启动

环境：.NET SDK 10.0.400以上同feature-band补丁版（见global.json）、Node.js 22.18+、Python 3.10+、Windows PowerShell 7.4+。Linux shell流程测试在Windows上需安装Git Bash。

在工程根目录按顺序执行：

```powershell
npm ci --prefix src/frontend
npm run build --prefix src/frontend
dotnet restore UpgradeDeployConsole.slnx --locked-mode
dotnet build UpgradeDeployConsole.slnx --no-restore
pwsh -NoProfile -File scripts/Start-DeployConsole.ps1
```

访问 http://127.0.0.1:5088 。首次运行生成`.local/deploy-console/password.txt`，初始密码`admin`；迁移来的已有密码和状态保留。`-Status`查看状态，`-Stop`停止本地面板。Windows只提供管理页和接口，真实systemd/Nginx/MySQL部署在Linux服务器执行。

## 验证

先完成以上构建；暂不启动常驻进程亦可运行测试。

```powershell
dotnet test tests/DeployConsole.Tests/DeployConsole.Tests.csproj --no-restore
python -m unittest discover -s tests/scripts -p "test_deploy_console*.py" -q
node --test tests/scripts/deploy-console-controls.test.mjs tests/scripts/deploy-console-form.test.mjs
```

Python HTTP测试会以临时目录启动独立API，不更改本机或服务器的真实项目。MySQL/systemd测试采用mock；真实生产验收需单独进行。

## 服务器部署

在根目录执行统一打包命令（自动构建前后端）：

```powershell
pwsh -NoProfile -File scripts/Package-DeployConsole.ps1
```

新包校验通过后自动删除旧包、旧解压目录、校验文件和历史交付附件。`deployment-artifacts/`只保留最新版`.tar.gz`、`upgrade-deploy-console.sh`和`DEPLOY.md`。以后也使用同一命令，具体规则见[打包说明](docs/deploy-console-package.md)。

上传最新压缩包与升级脚本到服务器`/root/`，执行生成的`DEPLOY.md`中的命令。不读取.sha256；服务器健康后清理本次包和核对匹配的解压目录。服务器环境及首次安装见[部署说明](docs/deploy-console.md)。

被部署工程需按[接入规范](docs/deploy-console-project-onboarding.md)提供自身的Git源码、`deploy-console.json`及迁移入口；面板源码根目录不放业务工程的接入描述。模板见`docs/deploy-console-onboarding-examples/`。

## 单独提交Git

本目录使用独立Git仓库，不包含原业务工程历史、远端或源码。提交前检查：

```powershell
git status --short
git add .
git diff --cached --stat
```

确认内容后自行提交并设置新仓库远端。`.local/`和`deployment-artifacts/`不参与提交，不要使用`git add -f`加入运行数据或密钥。

本次目录迁移及验证结果见[迁移说明](docs/standalone-migration.md)。
