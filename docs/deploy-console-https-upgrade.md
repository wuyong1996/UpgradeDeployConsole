# 默认域名与ACME证书HTTPS升级

> 下列历史版本的包已按用户要求不再保留。当前请按[最新打包与部署说明](deploy-console-package.md)获取完整包，并使用生成的DEPLOY.md安装；以下功能与排查说明继续适用。

在我们的部署面板中设置一次默认域名、完整证书链路径和私钥路径，即可给已有项目前端启用HTTPS，后续接入的前端自动使用相同配置。多个项目通过各自端口区分，例如 `https://www.halfsuger.top:18081/`。

本功能直接管理服务器现有Nginx的项目配置。支持系统Nginx和服务器已安装的宝塔Nginx，无需到宝塔页面配置项目SSL；不安装第二个Nginx。前端继续使用原端口，后端保持本机HTTP接口。

## 1. 上传并安装新版

用WinSCP将 `deploy-console-server-20260924-https-acme.tar.gz` 上传到 `/root/`。等当前发布任务完成后，在root终端执行：

先将新版`upgrade-deploy-console.sh`上传至`/root/`，覆盖旧脚本；无需上传校验文件。

```bash
bash /root/upgrade-deploy-console.sh /root/deploy-console-server-20260924-https-acme.tar.gz
```

新版脚本不读取或要求`.sha256`。升级包存在则安装，不存在则提示跳过安装；两种情况均在面板健康检查通过后，清理能够确认与当前安装内容一致的同版解压目录，以及本次包和可选的旧校验文件。不存在的文件直接跳过。支持本次临时目录、旧版随机解压目录及直接解压的同名上传目录；解压外层含其他文件、存在软链接、挂载点、其他版本或内容不符的目录会保留并说明原因。安装/健康检查失败时保留现场。旧压缩包内说明不更新，请使用这里的新脚本；不会把“缺包跳过”报告为“已升级”。

完整更新页面、API、TLS适配器和证书检查timer，保留已有密码、Git凭据、MySQL配置、项目状态及端口。服务器需要`/usr/bin/openssl`和系统CA根证书；安装器会检查OpenSSL。本次升级本身不修改业务数据库，也不自动填写尚未提供的实际证书路径。

## 2. 在面板配置

浏览器按`Ctrl + F5`，进入 **面板设置 → 默认域名与HTTPS**：

| 字段 | 填写内容 |
| --- | --- |
| 默认域名 | `www.halfsuger.top`，不含协议、端口和斜杠 |
| 完整证书链路径 | 服务器已有ACME完整链文件的绝对路径，例如`/root/.acme.sh/*.halfsuger.top_ecc/fullchain.cer` |
| 私钥路径 | 与上述证书对应的文件，例如`/root/.acme.sh/*.halfsuger.top_ecc/*.halfsuger.top.key` |

**上表路径对应用户提供的通配符证书目录截图；其他服务器按实际文件填写。** `.cer`和`.key`无需改名为`.pem`，面板按PEM文件内容校验。路径中的`*`是实际文件名的一部分，直接填写，不加引号、不替换成`www`；默认访问域名仍填`www.halfsuger.top`。此次修正版已修复上一版对星号路径的拒绝，必须完整更新API及tls.py，不能只更新网页。 RSA证书目录可能没有`_ecc`，通配符证书也可能保存在主域名目录。可以使用ACME已部署到`/etc/ssl/...`的稳定路径。文件必须为普通文件，不能是符号链接或硬链接；文件及父目录由root持有、不能由组或其他用户写入，私钥仅root可读（600）。不需要把证书或私钥内容粘贴到网页。

点击“验证并启用HTTPS”。面板检查域名覆盖、证书有效期/系统信任、完整证书链及私钥匹配，再生成受保护快照、更新本面板管理的前端站点，并执行Nginx配置检查和重载。其他站点配置不修改。若校验失败，原配置保留；若Nginx应用失败，会尝试还原项目配置并重载。

保存成功后返回项目，通过前端卡片的“访问地址”打开站点。例如现有前端18081仍使用18081，地址变为 `https://www.halfsuger.top:18081/`。普通HTTP请求会跳转到同端口HTTPS；已停止的前端仍保持停止。后端卡片标为“本机接口端口”，不作为公网访问入口。

## 3. 证书续期

由服务器原有ACME任务负责申请及续期，本面板不执行acme.sh或仓库提供的命令。安装的`deploy-console-https.timer`每15分钟检查源证书/私钥文件；内容变化时重新校验，通过后更新快照和项目Nginx配置。没有变化时不重载，服务器正在发布时等待下个周期。

续期文件无效、域名不符、过期、私钥权限不符或证书应用失败时保留上一份快照，在面板设置显示“证书同步失败”；旧证书仍受自身有效期限制。界面提供证书到期、最近应用及最近检查时间。修复源文件后，重新保存配置可立即同步，也可等待下一周期。

只读核对命令：

```bash
systemctl status deploy-console-https.timer --no-pager
journalctl -u deploy-console-https.service -n 30 --no-pager
grep -oE '/assets/index-[^" ]+\.js' /opt/deploy-console/current/wwwroot/index.html
```

本版页面脚本仍为`/assets/index-B6tw9X-O.js`；此次路径兼容修正未改前端，不能仅靠脚本名确认修正版已安装。可用`grep -nF 'A-Za-z0-9_.*-' /usr/local/lib/deploy-console/tls.py`检查服务器适配器包含星号路径规则，并在面板保存上表路径核验API也已更新。配置保存在`/etc/deploy-console/https.json`，受管证书快照保存在`/etc/deploy-console/tls/`，均由root保护，不进入Git或项目构建环境。

DNS仍需解析到本服务器，云安全组/服务器防火墙需允许所用前端端口。本机验证包括真实证书校验和HTTPS握手、模拟Nginx回滚及HTTP鉴权；Linux上实际Nginx重载、服务器ACME证书链和浏览器登录需安装后核对。

参考：[Nginx HTTPS配置](https://nginx.org/en/docs/http/configuring_https_servers.html)、[acme.sh证书部署说明](https://github.com/acmesh-official/acme.sh#3-install-the-cert-to-apachenginx-etc)。ACME官方推荐部署到稳定路径并在续期后重载，本面板通过快照及定时同步处理重载。
