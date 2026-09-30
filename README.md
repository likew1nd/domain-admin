# 域名管理系统

域名采集、备案 / WHOIS 批量查询、到期监控与多注册商自动抢注的后台管理系统。

前端基于 [SoybeanAdmin ElementPlus](https://github.com/soybeanjs/soybean-admin-element-plus)（Vue 3 + Vite + Element Plus），后端为 FastAPI + SQLite。

## 一键部署

需要一台 Linux 服务器（Ubuntu / Debian / CentOS 等，x86 与 ARM 均可），宝塔面板也适用，用 root 执行：

```bash
curl -fsSL https://raw.githubusercontent.com/likew1nd/domain-admin/main/deploy/install.sh | sudo bash
```

脚本会自动安装 Docker（已安装则跳过）、拉取镜像并启动。完成后访问 `http://服务器IP:8080`。

- 默认账号：`admin`，密码：`admin`，**登录后请立即在个人中心修改密码**
- 记得在云服务商安全组 / 宝塔「安全」中放行 8080 端口
- 更换端口：`curl -fsSL ... | sudo PORT=9000 bash`，或修改 `/opt/domain-admin/.env` 中的 `PORT` 后执行 `cd /opt/domain-admin && docker compose up -d`

### 宝塔面板绑定域名（可选）

1. 「网站 → 添加站点」，填写域名，PHP 版本选「纯静态」
2. 站点设置 →「反向代理」→ 添加，目标 URL 填 `http://127.0.0.1:8080`
3. 站点设置 →「SSL」申请证书并开启强制 HTTPS
4. 绑定域名后可以不再对外放行 8080 端口

## 更新

- **后台在线更新**：右上角显示当前版本，有新版本时出现红点，超级管理员点击「立即更新」即可，约 1 分钟后页面自动刷新
- **命令行更新**：`sudo bash /opt/domain-admin/install.sh update`

更新只替换程序镜像，数据不受影响。

## 数据与备份

所有数据位于 `/opt/domain-admin/data`：

| 文件 | 说明 |
| --- | --- |
| `domains.db` | 数据库（域名、任务、用户、配置） |
| `.cookie.key` | 采集源 Cookie / 注册商密钥的加密密钥，**必须与数据库一起备份** |
| `downloads/` | 采集下载的原始文件 |

备份：`cd /opt/domain-admin && docker compose stop app && tar czf ~/domain-backup-$(date +%F).tar.gz data && docker compose start app`

## 常用命令

```bash
cd /opt/domain-admin
docker compose ps              # 查看状态
docker compose logs -f app     # 查看日志
docker compose restart app     # 重启
docker compose down            # 停止并移除容器（数据保留）
```

## 开发

```bash
# 后端
cd backend
python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 前端（另开终端，/api 自动代理到 8000）
pnpm install
pnpm dev
```

## 发布新版本

1. 修改 `package.json` 中的 `version`，提交代码
2. 打标签并推送：`git tag v1.0.1 && git push origin main v1.0.1`

GitHub Actions 会构建 `linux/amd64`、`linux/arm64` 镜像推送到 `ghcr.io/likew1nd/domain-admin`，并创建 Release，已部署的系统会在后台提示更新。

## License

[MIT](./LICENSE)
