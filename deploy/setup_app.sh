#!/bin/bash
# ============================================================
# 第 2 步：部署八字选股应用（先跑完 setup_mysql.sh 并配好 .env）
# 用法：
#   sudo bash setup_app.sh                 # 默认部署 main 分支
#   sudo BRANCH=dev bash setup_app.sh      # 部署其他分支
# ============================================================
set -euo pipefail

REPO_URL="https://github.com/xueni97/bazixuangu-mysql.git"
INSTALL_DIR="/opt/bazixuangu"
BRANCH="${BRANCH:-main}"

if [ "$EUID" -ne 0 ]; then
  echo "[!] 请用 sudo 执行"
  exit 1
fi

echo "[1/7] 安装系统依赖..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl python3 python3-venv python3-pip nginx
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
  apt-get install -y -qq nodejs
fi

echo "[2/7] 拉取代码（分支 ${BRANCH}）..."
if [ -d "${INSTALL_DIR}/.git" ]; then
  git -C "${INSTALL_DIR}" fetch origin
  git -C "${INSTALL_DIR}" checkout "${BRANCH}"
  git -C "${INSTALL_DIR}" reset --hard "origin/${BRANCH}"
else
  git clone -b "${BRANCH}" "${REPO_URL}" "${INSTALL_DIR}"
fi

echo "[3/7] Python 虚拟环境 + 依赖..."
python3 -m venv "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/pip" install -q --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install -q -r "${INSTALL_DIR}/requirements.txt"

echo "[4/7] 检查 .env..."
if [ ! -f "${INSTALL_DIR}/.env" ]; then
  cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
  echo ""
  echo "[!] 已生成 ${INSTALL_DIR}/.env，请填入 MySQL 密码后重新运行本脚本："
  echo "      nano ${INSTALL_DIR}/.env"
  echo "      sudo bash setup_app.sh"
  exit 1
fi

echo "[5/7] 构建大屏前端..."
cd "${INSTALL_DIR}"
npm install --silent
npm run build

echo "[6/7] 安装 systemd 服务..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp -f "${SCRIPT_DIR}/bazi-api.service" /etc/systemd/system/bazi-api.service
systemctl daemon-reload
systemctl enable bazi-api
systemctl restart bazi-api

echo "[7/7] 配置 Nginx..."
cp -f "${SCRIPT_DIR}/nginx-bazi-api.conf" /etc/nginx/sites-available/bazi-api
ln -sf /etc/nginx/sites-available/bazi-api /etc/nginx/sites-enabled/bazi-api
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
systemctl restart bazi-api

sleep 3
echo ""
echo "=========================================="
echo "  应用部署完成"
echo "=========================================="
echo "健康检查: curl http://127.0.0.1:5175/api/health"
echo "外网访问: http://<你的公网IP>/  （需在腾讯云防火墙放行 80 端口）"
echo "服务日志: journalctl -u bazi-api -f"
echo ""
echo "首次使用请在大屏页面点「数据管理 → 更新快照/日均线」"
echo "（或 curl -XPOST http://127.0.0.1:5175/api/sync）"
