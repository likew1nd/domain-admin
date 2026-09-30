#!/usr/bin/env bash
# 域名管理系统一键安装 / 更新脚本
#   安装：curl -fsSL https://raw.githubusercontent.com/likew1nd/domain-admin/main/deploy/install.sh | sudo bash
#   更新：sudo bash /opt/domain-admin/install.sh update
# 可选环境变量：APP_DIR（安装目录，默认 /opt/domain-admin）、PORT（访问端口，默认 8080）
set -euo pipefail

REPO="likew1nd/domain-admin"
APP_DIR="${APP_DIR:-/opt/domain-admin}"
PORT="${PORT:-8080}"
RAW="https://raw.githubusercontent.com/${REPO}/main/deploy"
ACTION="${1:-install}"

info() { printf '\033[32m[INFO]\033[0m %s\n' "$*"; }
fail() { printf '\033[31m[ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "请使用 root 运行（在命令前加 sudo）"

install_docker() {
  if command -v docker >/dev/null 2>&1; then
    return
  fi
  info "未检测到 Docker，开始安装（国内服务器使用阿里云镜像源）..."
  curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun \
    || fail "Docker 安装失败。宝塔用户可在「软件商店 → Docker管理器」安装后重新运行本脚本"
  systemctl enable --now docker
}

check_compose() {
  docker compose version >/dev/null 2>&1 \
    || fail "当前 Docker 缺少 compose 插件，请升级 Docker（或在宝塔 Docker管理器 中更新）后重试"
}

download() {
  curl -fsSL "${RAW}/$1" -o "$2.tmp" || fail "下载 $1 失败，请检查服务器能否访问 raw.githubusercontent.com"
  mv "$2.tmp" "$2"
}

install_docker
check_compose
mkdir -p "${APP_DIR}/data"
cd "${APP_DIR}"

download docker-compose.yml docker-compose.yml
download install.sh install.sh
chmod +x install.sh

gen_token() { head -c 24 /dev/urandom | od -An -tx1 | tr -d " \n"; }

if [ ! -f .env ]; then
  cat > .env <<ENV
# 访问端口：浏览器访问 http://服务器IP:端口
PORT=${PORT}
# 后台在线更新调用 watchtower 的内部令牌
UPDATE_TOKEN=$(gen_token)
ENV
elif ! grep -q '^UPDATE_TOKEN=' .env; then
  # 旧版本安装升级：补充令牌
  echo "UPDATE_TOKEN=$(gen_token)" >> .env
fi

info "拉取镜像并启动..."
docker compose pull
docker compose up -d --remove-orphans
docker image prune -f >/dev/null 2>&1 || true

PORT_NOW="$(grep -E '^PORT=' .env | cut -d= -f2)"
IP="$(curl -fsS --max-time 3 https://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')"
echo
if [ "${ACTION}" = "update" ]; then
  info "更新完成"
else
  info "安装完成！"
  info "访问地址：http://${IP}:${PORT_NOW}"
  info "默认账号：admin  密码：admin（登录后请立即修改）"
  info "请在服务器防火墙 / 宝塔「安全」/ 云服务商安全组中放行端口 ${PORT_NOW}"
fi
info "安装目录：${APP_DIR}，数据目录：${APP_DIR}/data（备份此目录即可）"
