# 域名管理系统

域名采集、备案 / 拦截批量查询、到期监控与多注册商自动抢注的后台管理系统。

[![release](https://img.shields.io/github/v/release/likew1nd/domain-admin)](https://github.com/likew1nd/domain-admin/releases)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)

## 功能

- **过期域名采集**：从 GNAME、西部数码等来源按日自动采集删除域名，也支持上传文件导入
- **域名查询**：批量查询工信部备案、微信 / QQ 拦截、DNS 污染、墙、黑名单状态，自动区分「符合 / 不符合」
- **监控抢注**：周期检查符合域名的 WHOIS 状态，一旦可注册即并行调用已启用的注册商 API 下单（GNAME、GoDaddy、Dynadot、阿里云国际等）；已被续费或注册的域名自动踢出并记录原因
- **域名生成**：按字典组合生成候选域名并导入查询
- **系统管理**：用户、角色、菜单权限，图形验证码、登录锁定、注册审核、邮箱登录与找回密码
- **在线更新**：右上角显示当前版本，有新版本时一键更新

## 部署

需要一台 Linux 服务器（Ubuntu / Debian / CentOS 等，x86 与 ARM 均可）。以下两种方式任选其一。

### 方式一：一键脚本（推荐）

用 root 执行（宝塔用户在「终端」中执行）：

```bash
curl -fsSL https://raw.githubusercontent.com/likew1nd/domain-admin/main/deploy/install.sh | sudo bash
```

脚本会自动安装 Docker（已安装则跳过）、拉取镜像并启动，程序安装在 `/opt/domain-admin`。

要换端口：`curl -fsSL https://raw.githubusercontent.com/likew1nd/domain-admin/main/deploy/install.sh | sudo PORT=9000 bash`

### 方式二：宝塔「容器编排」

1. 宝塔「Docker」中确认已安装 Docker
2. 「Docker → 容器编排 → 添加」，名称填 `domain-admin`
3. 把 [deploy/docker-compose.yml](./deploy/docker-compose.yml) 的内容整段粘贴进去，**无需修改**，点确定

编排的「环境变量」（.env）中可选填写：

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `PORT` | 访问端口 | `8080` |
| `UPDATE_TOKEN` | 在线更新的内部令牌，仅在两个容器间使用，建议填一个长随机字符串 | 内置默认值 |

也可以不用宝塔：把 `docker-compose.yml` 放到任意目录，执行 `docker compose up -d`。

### 部署完成后

1. 在云服务商安全组、宝塔「安全」中放行端口（默认 8080）
2. 访问 `http://服务器IP:8080`，默认账号 `admin`，密码 `admin`
3. **登录后立即在「个人中心」修改密码**

### 绑定域名与 HTTPS（可选）

1. 宝塔「网站 → 添加站点」，填写域名，PHP 版本选「纯静态」
2. 站点设置 →「反向代理」→ 添加，目标 URL 填 `http://127.0.0.1:8080`
3. 站点设置 →「SSL」申请证书，开启「强制 HTTPS」
4. 之后可以在安全组中关闭 8080 端口，只通过域名访问

## 更新

**后台在线更新（推荐）**：有新版本时右上角版本号旁出现红点，点开可查看更新内容，超级管理员点击「立即更新」，约 1 分钟后页面自动刷新。

**手动更新**：

| 部署方式 | 操作 |
| --- | --- |
| 一键脚本 | `sudo bash /opt/domain-admin/install.sh update` |
| 容器编排 | 宝塔编排页点「更新镜像」，或在编排目录执行 `docker compose pull && docker compose up -d` |

更新只替换程序镜像，数据不受影响。

> [!NOTE]
> v1.0.0 的在线更新机制与之后的版本不兼容。从 v1.0.0 升级请手动执行一次上面的更新命令，之后即可在后台一键更新。

## 数据与备份

数据保存在部署目录下的 `data` 文件夹：

| 部署方式 | 数据目录 |
| --- | --- |
| 一键脚本 | `/opt/domain-admin/data` |
| 容器编排 | 编排目录下的 `data`（宝塔一般为 `/www/dk_project/dk_app/domain-admin/data`） |

| 文件 | 说明 |
| --- | --- |
| `domains.db` | 数据库：域名、任务、用户、配置 |
| `.cookie.key` | 采集源 Cookie、注册商密钥的加密密钥，**必须与数据库一起备份**，丢失后已保存的密钥无法解密 |
| `downloads/` | 采集下载的原始文件 |

备份（在部署目录执行，先停止服务保证数据库完整）：

```bash
docker compose stop app
tar czf ~/domain-backup-$(date +%F).tar.gz data
docker compose start app
```

恢复：停止服务，用备份覆盖 `data` 目录后再启动。

## 常用命令

在部署目录（`/opt/domain-admin` 或编排目录）执行：

```bash
docker compose ps                 # 查看运行状态
docker compose logs -f app        # 查看程序日志
docker logs domain-admin-updater  # 查看在线更新日志
docker compose restart app        # 重启
docker compose down               # 停止并删除容器（数据保留）
```

## 常见问题

**无法访问页面**：检查安全组 / 宝塔防火墙是否放行端口，并执行 `docker compose ps` 确认 `domain-admin` 状态为 `healthy`。

**安装时拉取镜像失败**：服务器需要能访问 `ghcr.io` 和 `raw.githubusercontent.com`，国内服务器网络不稳定时可重试几次。

**忘记管理员密码**：如果配置了邮箱，可在登录页找回密码；否则在部署目录执行下面的命令，把 `admin` 的密码重置为 `admin`：

```bash
docker compose exec app python -c "from app import db, system_manage as s; db.execute('UPDATE sys_users SET password_hash=?, failed_attempts=0, locked_until=\'\' WHERE user_name=?', (s.hash_password('admin'), 'admin'))"
```

**在线更新按钮不可用**：说明没有检测到 `domain-admin-updater` 容器，请确认使用的是仓库中的 `docker-compose.yml` 部署，并执行 `docker compose up -d` 确保两个容器都在运行。

## 开发

环境要求：Node.js ≥ 20.19、pnpm ≥ 10、Python ≥ 3.10。

```bash
# 后端
cd backend
python -m venv .venv
.venv\Scripts\activate          # Linux / macOS：source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 前端（另开终端，/api 请求自动代理到 8000 端口）
pnpm install
pnpm dev
```

本地全新启动时同样会创建 `admin / admin` 账号，数据保存在项目根目录的 `data`。

## 发布新版本

1. 修改 `package.json` 中的 `version`（例如 `1.0.2`），提交并推送代码
2. 打标签并推送：

   ```bash
   git tag v1.0.2
   git push origin v1.0.2
   ```

GitHub Actions 会自动构建 `linux/amd64`、`linux/arm64` 镜像并推送到 `ghcr.io/likew1nd/domain-admin`，同时创建 Release，更新说明为自上个版本以来的提交记录。已部署的系统约 10 分钟内会在后台提示更新。

## 技术栈

- 前端：Vue 3、Vite、TypeScript、Element Plus、UnoCSS，基于 [SoybeanAdmin ElementPlus](https://github.com/soybeanjs/soybean-admin-element-plus)
- 后端：FastAPI、SQLite
- 部署：Docker、GitHub Actions、[Watchtower](https://github.com/nicholas-fedor/watchtower)（在线更新）

## License

[MIT](./LICENSE)
