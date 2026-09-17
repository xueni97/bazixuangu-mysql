# 云服务器部署指南（Ubuntu 22.04 + MySQL 8）

> 适用：腾讯云轻量应用服务器（北京），公网 IP 以控制台实际显示为准。
> 全程约 20 分钟，照做即可。

---

## 第 0 步：登录服务器（还不会登录看这里）

1. 打开 https://console.cloud.tencent.com/lighthouse
2. 点实例名进入详情 → 右上角 **更多 → 重置密码**，设一个自己记得住的密码 → 重启实例
3. 点 **登录**，弹出的网页终端里输入用户名 `root` 和刚设的密码

看到 `root@Ubuntu-d2en:~#` 提示符就是登录成功了。

---

## 第 1 步：下载代码并安装 MySQL

```bash
# 拉取代码（MySQL 版在 feature/mysql 分支）
cd /opt
git clone -b feature/mysql https://github.com/xueni97/bazixuangu.git
cd bazixuangu/deploy

# 如果 GitHub 很慢/超时，改用镜像：
# git clone -b feature/mysql https://ghproxy.com/https://github.com/xueni97/bazixuangu.git

# 安装 MySQL 并创建数据库/账号（会让你输入数据库密码，自己定一个强密码）
sudo bash setup_mysql.sh
```

脚本结束会打印数据库名/用户名，**记住密码**。

---

## 第 2 步：填写数据库配置

```bash
cd /opt/bazixuangu
cp .env.example .env
nano .env
```

把内容改成第 1 步设置的真实信息：

```env
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=bazi
DB_PASSWORD=你刚才设置的密码
DB_NAME=bazixuangu
```

`nano` 保存：`Ctrl+O` 回车 → `Ctrl+X` 退出。

---

## 第 3 步：部署应用

```bash
cd /opt/bazixuangu/deploy
sudo bash setup_app.sh
```

脚本自动完成：装 Python/Node/Nginx → 装依赖 → 构建大屏页面 →
启动 gunicorn 常驻服务 → 配置 Nginx（80 端口反代）。

验证：

```bash
# 在服务器上
curl http://127.0.0.1:5175/api/health
# 应返回 {"status":"ok", ...}

# 手动触发一次全市场行情快照（后台跑，约1~3分钟）
curl -X POST http://127.0.0.1:5175/api/sync
# 查看进度
curl http://127.0.0.1:5175/api/sync/status
```

---

## 第 4 步：放行云防火墙端口

腾讯云控制台 → 轻量服务器 → **防火墙** → 添加规则：

| 端口 | 用途 |
|------|------|
| 80 | 网页访问（Nginx）|
| 22 | SSH 远程登录（默认已开）|

然后浏览器打开：**http://你的公网IP/** 即可看到大屏。
用 IP 直接访问不需要备案。

首次进入后到「数据管理」点更新快照和日均线，数据就齐了。

---

## 日常运维命令

```bash
# 查看后端实时日志
sudo journalctl -u bazi-api -f

# 重启 / 停止 / 启动
sudo systemctl restart bazi-api
sudo systemctl stop bazi-api
sudo systemctl start bazi-api

# 更新代码后重新部署
cd /opt/bazixuangu && git pull
cd deploy && sudo bash setup_app.sh

# 进 MySQL 看数据
mysql -ubazi -p bazixuangu
# SHOW TABLES;
# SELECT COUNT(*) FROM stock_spot;
```

---

## 目录与架构

```
/opt/bazixuangu/
├── server/
│   ├── app.py             # Flask API + 大屏托管
│   ├── data_sync.py       # 行情快照/均线同步（东财→腾讯→新浪 fallback）
│   ├── db.py              # MySQL 连接与建库建表
│   └── metaphysics/       # 八字命理引擎（自包含，不依赖外部项目）
├── src/                   # Vue 前端（Capacitor APP 同源代码）
├── dist/                  # 前端构建产物（Flask 托管）
├── .venv/                 # Python 虚拟环境
├── .env                   # 数据库配置（不入 git）
└── requirements.txt
```

- 服务进程：gunicorn（systemd 托管，开机自启、崩了自动拉起）
- 数据库：MySQL 8（本机 127.0.0.1，不对外暴露）
- 入口：Nginx :80 → gunicorn :5175

---

## 常见问题

**Q: setup_mysql.sh 后应用连不上数据库？**
确认 `.env` 里的密码与设置的一致；手动验证：
`mysql -ubazi -p -h127.0.0.1 -e "SELECT 1"`

**Q: 80 端口打不开？**
99% 是腾讯云防火墙没放行 80，或 setup_app.sh 没跑完。
检查：`sudo systemctl status bazi-api nginx`

**Q: 想改回直连 5175 端口？**
腾讯云防火墙放行 5175，然后 http://公网IP:5175 访问（不经 Nginx）。

**Q: 数据库要定期备份？**
`mysqldump -ubazi -p bazixuangu > backup_$(date +%F).sql`，可加到 crontab。
