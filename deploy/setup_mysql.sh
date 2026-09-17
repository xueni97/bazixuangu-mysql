#!/bin/bash
# ============================================================
# 第 1 步：在腾讯云 Ubuntu 22.04 上安装 MySQL 8 并创建业务库/账号
# 用法（在项目 deploy/ 目录下）：
#   sudo DB_PASSWORD='你的强密码' bash setup_mysql.sh
#   或直接 sudo bash setup_mysql.sh（会交互式提示输入密码）
# ============================================================
set -euo pipefail

DB_NAME="${DB_NAME:-bazixuangu}"
DB_USER="${DB_USER:-bazi}"
DB_PASSWORD="${DB_PASSWORD:-}"

if [ "$EUID" -ne 0 ]; then
  echo "[!] 请用 sudo 执行"
  exit 1
fi

if [ -z "$DB_PASSWORD" ]; then
  read -sp "请为数据库用户 ${DB_USER} 设置密码（输入时不显示）: " DB_PASSWORD
  echo
  if [ -z "$DB_PASSWORD" ]; then
    echo "[!] 密码不能为空"
    exit 1
  fi
fi

echo "[1/3] 安装 MySQL Server..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq mysql-server >/dev/null
systemctl enable --now mysql
sleep 2

echo "[2/3] 创建数据库与用户..."
# Ubuntu 的 root 通过 debian-sys-maint 免密登录
mysql --defaults-file=/etc/mysql/debian.cnf <<SQL
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}_test\`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
CREATE USER IF NOT EXISTS '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${DB_PASSWORD}';
ALTER USER '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
ALTER USER '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.*      TO '${DB_USER}'@'localhost';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.*      TO '${DB_USER}'@'127.0.0.1';
GRANT ALL PRIVILEGES ON \`${DB_NAME}_test\`.* TO '${DB_USER}'@'localhost';
GRANT ALL PRIVILEGES ON \`${DB_NAME}_test\`.* TO '${DB_USER}'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL

echo "[3/3] 验证连接..."
if mysql -u"${DB_USER}" -p"${DB_PASSWORD}" -h127.0.0.1 -e \
    "SELECT 'MySQL 连接成功' AS result; SHOW DATABASES LIKE '${DB_NAME}';" ; then
  echo ""
  echo "=========================================="
  echo "  MySQL 初始化完成"
  echo "  库名: ${DB_NAME}（测试库: ${DB_NAME}_test）"
  echo "  用户: ${DB_USER}"
  echo "=========================================="
  echo ""
  echo "下一步：把下面这段写进项目 .env"
  echo "  DB_HOST=127.0.0.1"
  echo "  DB_PORT=3306"
  echo "  DB_USER=${DB_USER}"
  echo "  DB_PASSWORD=${DB_PASSWORD}"
  echo "  DB_NAME=${DB_NAME}"
else
  echo "[!] 连接验证失败，请检查密码和 MySQL 状态: systemctl status mysql"
  exit 1
fi
