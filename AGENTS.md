# AGENTS.md

- 默认用中文沟通，修改范围聚焦服务器更新管理系统。
- 开始前读取README、TASKS，检查Git状态，保留用户无关修改。
- 沿用.NET 10、Vue 3/TypeScript、Python标准库；不引入培训报名等业务工程的编译引用。
- 所有源码和测试路径从本仓库推导，不硬编码开发机绝对路径；被部署工程按接入协议独立维护。
- 前端构建必须先于.NET构建。运行命令及测试见README；按任务风险选择验证，只报告实际执行结果。
- 本地进程使用scripts/Start-DeployConsole.ps1管理，真实Linux/MySQL功能不能以mock通过冒充生产验收。
- 不提交.local、密码、Token、证书、备份、node_modules、编译输出或deployment-artifacts；保留锁文件和非敏感示例。
- 不擅自提交、推送或操作正式服务器；用户要求的动作按当次授权执行。

- 用户于2026-09-24明确要求只保留最新部署交付；后续生成部署包统一使用scripts/Package-DeployConsole.ps1，成功核验后清理旧包/解压目录/旧附件，不另外保留历史副本。失败不得删除此前可用包。
