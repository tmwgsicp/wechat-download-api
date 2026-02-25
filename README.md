<div align="center">

# WeChat Download API

### 微信公众号文章获取 & RSS 订阅服务

**完全开源 | 免费部署 | RSS 订阅 | 文章抓取 | 反风控**

[![GitHub stars](https://img.shields.io/github/stars/tmwgsicp/wechat-download-api?style=for-the-badge&logo=github)](https://github.com/tmwgsicp/wechat-download-api/stargazers)
[![License](https://img.shields.io/badge/License-AGPL%203.0-blue?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)

> **100% 开源，100% 免费。** 代码完全公开，私有化部署无任何限制，不搞"开源"之名行收费之实。

</div>

---

## 功能特性

- **RSS 订阅** — 订阅任意公众号，自动定时拉取新文章，生成标准 RSS 2.0 源，接入 FreshRSS / Feedly 等阅读器即可使用
- **文章内容获取** — 通过 URL 获取文章完整内容（标题、作者、正文 HTML / 纯文本、图片列表）
- **反风控体系** — Chrome TLS 指纹模拟 + IP 代理池轮转 + 三层自动限频，有效对抗微信封控
- **文章列表 & 搜索** — 获取任意公众号历史文章列表，支持分页和关键词搜索
- **公众号搜索** — 按名称搜索公众号，获取 FakeID
- **扫码登录** — 微信公众平台扫码登录，凭证自动保存，4 天有效期
- **图片代理** — 代理微信 CDN 图片，解决防盗链问题
- **Webhook 通知** — 登录过期、触发验证等事件自动推送（支持企业微信机器人）
- **API 文档** — 自动生成 Swagger UI / ReDoc，在线调试所有接口

<div align="center">
  <img src="assets/dashboard.jpg" width="800" alt="管理面板">
  <p><em>管理面板 — 登录状态、接口文档、在线测试一站式管理</em></p>
  <br>
  <img src="assets/rss.jpg" width="800" alt="RSS 订阅管理">
  <p><em>RSS 订阅管理 — 搜索公众号一键订阅，复制地址接入 RSS 阅读器</em></p>
</div>

---

## SaaS 托管版（即将推出）

不想自己部署？我们正在筹备 **RSS 订阅托管服务**——无需服务器、无需配置，输入公众号名称即可获得 RSS 订阅地址，直接接入你喜欢的 RSS 阅读器。同时也在评估开放文章内容获取 API 的托管方案。

感兴趣的话欢迎扫码添加微信，提前锁定体验名额 👇 [联系方式](#联系方式)

---

## 使用前提

> 本工具需要通过微信公众平台后台的登录凭证来调用接口，因此使用前需要：

1. **拥有一个微信公众号**（订阅号、服务号均可）
2. 部署并启动服务后，访问登录页面用**公众号管理员微信**扫码登录
3. 登录成功后凭证自动保存到 `.env` 文件，有效期约 **4 天**，过期后需重新扫码

登录后即可通过 API 获取**任意公众号**的公开文章（不限于自己的公众号）。

---

## 快速开始

### 方式一：一键启动（推荐）

**Windows：**
```bash
start.bat
```

**Linux / macOS：**
```bash
chmod +x start.sh
./start.sh
```

脚本会自动完成环境检查、虚拟环境创建、依赖安装和服务启动。

> Linux 生产环境可使用 `sudo bash start.sh` 自动配置 systemd 服务和开机自启。

### 方式二：手动安装

```bash
# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# 安装依赖
pip install -r requirements.txt

# 启动
python app.py
```

### 访问服务

| 地址 | 说明 |
|------|------|
| http://localhost:5000 | 管理面板 |
| http://localhost:5000/login.html | 扫码登录 |
| http://localhost:5000/api/docs | Swagger API 文档 |
| http://localhost:5000/api/health | 健康检查 |

---

## API 接口

### 获取文章内容

`POST /api/article` — 解析微信公众号文章，返回标题、正文、图片等结构化数据

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | string | 是 | 微信文章链接（`https://mp.weixin.qq.com/s/...`） |

请求示例：

```bash
curl -X POST http://localhost:5000/api/article \
  -H "Content-Type: application/json" \
  -d '{"url": "https://mp.weixin.qq.com/s/xxxxx"}'
```

返回字段：`title` 标题、`content` HTML 正文、`plain_content` 纯文本正文、`author` 作者、`publish_time` 发布时间戳、`images` 图片列表

### 搜索公众号

`GET /api/public/searchbiz` — 按关键词搜索微信公众号，获取 FakeID

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | 是 | 搜索关键词（公众号名称） |

请求示例：

```bash
curl "http://localhost:5000/api/public/searchbiz?query=公众号名称"
```

返回字段：`list[]` 公众号列表，每项包含 `fakeid`、`nickname`、`alias`、`round_head_img`

### 获取文章列表

`GET /api/public/articles` — 获取指定公众号的文章列表，支持分页

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `fakeid` | string | 是 | 目标公众号的 FakeID（从搜索接口获取） |
| `begin` | int | 否 | 偏移量，默认 `0` |
| `count` | int | 否 | 获取数量，默认 `10`，最大 `100` |
| `keyword` | string | 否 | 在该公众号内搜索关键词 |

请求示例：

```bash
# 获取前 50 篇
curl "http://localhost:5000/api/public/articles?fakeid=YOUR_FAKEID&begin=0&count=50"

# 获取第 51-100 篇
curl "http://localhost:5000/api/public/articles?fakeid=YOUR_FAKEID&begin=50&count=50"
```

### 搜索公众号文章

`GET /api/public/articles/search` — 在指定公众号内按关键词搜索文章

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `fakeid` | string | 是 | 目标公众号的 FakeID |
| `query` | string | 是 | 搜索关键词 |
| `begin` | int | 否 | 偏移量，默认 `0` |
| `count` | int | 否 | 获取数量，默认 `10`，最大 `100` |

请求示例：

```bash
curl "http://localhost:5000/api/public/articles/search?fakeid=YOUR_FAKEID&query=关键词"
```

### RSS 订阅

`GET /api/rss/{fakeid}` — 获取指定公众号的 RSS 2.0 订阅源

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `fakeid` | string（路径） | 是 | 公众号 FakeID |
| `limit` | int（查询） | 否 | 返回文章数量上限，默认 `20` |

使用方式：

```bash
# 1. 搜索公众号获取 fakeid
curl "http://localhost:5000/api/public/searchbiz?query=人民日报"
# 返回 fakeid: MzA1MjM1ODk2MA==

# 2. 添加订阅
curl -X POST http://localhost:5000/api/rss/subscribe \
  -H "Content-Type: application/json" \
  -d '{"fakeid": "MzA1MjM1ODk2MA==", "nickname": "人民日报"}'

# 3. 手动触发一次轮询（立即拉取文章）
curl -X POST http://localhost:5000/api/rss/poll

# 4. 获取 RSS 源（把这个地址添加到 RSS 阅读器）
curl "http://localhost:5000/api/rss/MzA1MjM1ODk2MA=="
```

也可以通过管理面板的 **RSS 订阅** 页面可视化管理，搜索公众号一键订阅并复制 RSS 地址。

#### RSS 订阅管理接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/rss/subscribe` | 添加 RSS 订阅 |
| `DELETE` | `/api/rss/subscribe/{fakeid}` | 取消 RSS 订阅 |
| `GET` | `/api/rss/subscriptions` | 获取订阅列表 |
| `POST` | `/api/rss/poll` | 手动触发轮询 |
| `GET` | `/api/rss/status` | 轮询器状态 |

### 其他接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/image?url=IMG_URL` | 图片代理（仅限微信 CDN 域名） |
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/stats` | 限频统计 |
| `POST` | `/api/login/session/{id}` | 初始化登录会话 |
| `GET` | `/api/login/getqrcode` | 获取登录二维码 |
| `GET` | `/api/login/scan` | 检查扫码状态 |
| `POST` | `/api/login/bizlogin` | 完成登录 |
| `GET` | `/api/login/info` | 获取登录信息 |
| `GET` | `/api/admin/status` | 查询登录状态 |
| `POST` | `/api/admin/logout` | 退出登录 |

完整的接口文档请访问 http://localhost:5000/api/docs

---

## 配置说明

复制 `env.example` 为 `.env`，登录后凭证会自动保存：

```bash
cp env.example .env
```

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `WECHAT_TOKEN` | 微信 Token（登录后自动填充） | - |
| `WECHAT_COOKIE` | 微信 Cookie（登录后自动填充） | - |
| `WECHAT_FAKEID` | 公众号 FakeID（登录后自动填充） | - |
| `WEBHOOK_URL` | Webhook 通知地址（可选） | 空 |
| `RATE_LIMIT_GLOBAL` | 全局每分钟请求上限 | 10 |
| `RATE_LIMIT_PER_IP` | 单 IP 每分钟请求上限 | 5 |
| `RATE_LIMIT_ARTICLE_INTERVAL` | 文章请求最小间隔（秒） | 3 |
| `RSS_POLL_INTERVAL` | RSS 轮询间隔（秒） | 3600 |
| `PROXY_URLS` | 代理池地址（多个逗号分隔，留空直连） | 空 |
| `PORT` | 服务端口 | 5000 |
| `HOST` | 监听地址 | 0.0.0.0 |
| `DEBUG` | 调试模式（开启热重载） | false |

### 代理池配置（可选）

文章内容获取接口（`POST /api/article`）会访问微信文章页面，频繁请求可能触发微信验证码保护。配置代理池可以将请求分散到不同 IP，降低风控风险。

> 本项目使用 `curl_cffi` 模拟 Chrome TLS 指纹，请求特征与真实浏览器一致，配合代理池效果更佳。

**方案：多台 VPS 自建 SOCKS5 代理**

准备 2-3 台低价 VPS（各大云厂商轻量应用服务器即可，¥20-30/月/台），每台运行一个 SOCKS5 代理服务。推荐 [gost](https://github.com/go-gost/gost)（Go 语言实现，单二进制文件，无依赖）。

**第一步：在每台 VPS 上安装 gost**

```bash
# 下载最新版（以 Linux amd64 为例，其他架构请去 GitHub Releases 页面选择）
# 国外服务器直接下载
wget https://github.com/go-gost/gost/releases/download/v3.2.6/gost_3.2.6_linux_amd64.tar.gz

# 国内服务器使用加速镜像（任选一个可用的）
wget https://gh-proxy.com/https://github.com/go-gost/gost/releases/download/v3.2.6/gost_3.2.6_linux_amd64.tar.gz
# 或
wget https://ghproxy.cc/https://github.com/go-gost/gost/releases/download/v3.2.6/gost_3.2.6_linux_amd64.tar.gz

# 解压并移动到系统路径
tar -xzf gost_3.2.6_linux_amd64.tar.gz
mv gost /usr/local/bin/
chmod +x /usr/local/bin/gost

# 验证安装
gost -V
```

**第二步：启动 SOCKS5 代理服务**

```bash
# 带用户名密码认证（推荐，替换 myuser / mypass 和端口）
gost -L socks5://myuser:mypass@:1080

# 不带认证（仅内网或已配置防火墙时使用）
gost -L socks5://:1080
```

**第三步：配置为 systemd 服务（开机自启）**

```bash
cat > /etc/systemd/system/gost.service << 'EOF'
[Unit]
Description=GOST Proxy
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/gost -L socks5://myuser:mypass@:1080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable gost
systemctl start gost
```

**第四步：开放防火墙端口**

```bash
# 仅允许你的主服务器 IP 连接（替换为实际 IP）
ufw allow from YOUR_MAIN_SERVER_IP to any port 1080

# 或者如果用的是云厂商安全组，在控制台添加入站规则：
# 端口 1080 / TCP / 来源 IP 限制为你的主服务器
```

**第五步：在主服务器 `.env` 中配置代理池**

```bash
PROXY_URLS=socks5://myuser:mypass@vps1-ip:1080,socks5://myuser:mypass@vps2-ip:1080,socks5://myuser:mypass@vps3-ip:1080
```

配置后重启服务，每次文章请求会轮流使用不同的代理 IP。可以通过 `GET /api/health` 确认代理池状态。留空则直连（默认行为）。

---

## 项目结构

```
├── app.py                # FastAPI 主应用
├── requirements.txt      # Python 依赖
├── env.example           # 环境变量示例
├── data/                 # 数据目录（运行时自动创建）
│   └── rss.db            # RSS 订阅 SQLite 数据库
├── routes/               # API 路由
│   ├── article.py        # 文章内容获取
│   ├── articles.py       # 文章列表
│   ├── rss.py            # RSS 订阅管理与输出
│   ├── search.py         # 公众号搜索
│   ├── login.py          # 扫码登录
│   ├── admin.py          # 管理接口
│   ├── image.py          # 图片代理
│   ├── health.py         # 健康检查
│   └── stats.py          # 统计信息
├── utils/                # 工具模块
│   ├── auth_manager.py   # 认证管理
│   ├── helpers.py        # HTML 解析
│   ├── http_client.py    # HTTP 客户端（curl_cffi + 代理池）
│   ├── proxy_pool.py     # 代理池轮转
│   ├── rate_limiter.py   # 限频器
│   ├── rss_store.py      # RSS 数据存储（SQLite）
│   ├── rss_poller.py     # RSS 后台轮询器
│   └── webhook.py        # Webhook 通知
└── static/               # 前端页面（含 RSS 管理）
```

---

## 常见问题

<details>
<summary><b>提示"服务器未登录"</b></summary>

访问 http://localhost:5000/login.html 扫码登录，凭证会自动保存到 `.env`。
</details>

<details>
<summary><b>触发微信风控 / 需要验证</b></summary>

1. 在浏览器中打开提示的文章 URL 完成验证
2. 等待 30 分钟后重试
3. 降低请求频率（系统已内置自动限频）
</details>

<details>
<summary><b>如何获取公众号的 FakeID</b></summary>

调用搜索接口：`GET /api/public/searchbiz?query=公众号名称`，从返回结果的 `fakeid` 字段获取。
</details>

<details>
<summary><b>Token 多久过期</b></summary>

Cookie 登录有效期约 4 天，过期后需重新扫码登录。配置 `WEBHOOK_URL` 可以在过期时收到通知。
</details>

<details>
<summary><b>可以同时登录多个公众号吗</b></summary>

当前版本不支持多账号。建议部署多个实例，每个登录不同公众号。
</details>

---

## 技术栈

| 层级 | 技术 |
|------|------|
| **Web 框架** | FastAPI |
| **ASGI 服务器** | Uvicorn |
| **HTTP 客户端** | curl_cffi（Chrome TLS 指纹）/ HTTPX（降级） |
| **反风控** | TLS 指纹模拟 + SOCKS5/HTTP 代理池轮转 |
| **RSS 存储** | SQLite（零配置，数据本地化） |
| **配置管理** | python-dotenv |
| **运行环境** | Python 3.8+ |

---

## 开源协议

本项目采用 **AGPL 3.0** 协议开源，**所有功能代码完整公开，私有化部署完全免费**。

| 使用场景 | 是否允许 |
|---------|---------|
| 个人学习和研究 | 允许，免费使用 |
| 企业内部使用 | 允许，免费使用 |
| 私有化部署 | 允许，免费使用 |
| 修改后对外提供网络服务 | 需开源修改后的代码 |

详见 [LICENSE](LICENSE) 文件。

### 免责声明

- 本软件按"原样"提供，不提供任何形式的担保
- 本项目仅供学习和研究目的，请遵守微信公众平台相关服务条款
- 使用者对自己的操作承担全部责任
- 因使用本软件导致的任何损失，开发者不承担责任

---

## 参与贡献

由于个人精力有限，目前**暂不接受 PR**，但非常欢迎：

- **提交 Issue** — 报告 Bug、提出功能建议
- **Fork 项目** — 自由修改和定制
- **Star 支持** — 给项目点 Star，让更多人看到

---

## 联系方式

<table>
  <tr>
    <td align="center">
      <img src="assets/qrcode/wechat.jpg" width="200"><br>
      <b>个人微信</b><br>
      <em>技术交流 · 商务合作</em>
    </td>
    <td align="center">
      <img src="assets/qrcode/sponsor.jpg" width="200"><br>
      <b>赞赏支持</b><br>
      <em>开源不易，感谢支持</em>
    </td>
  </tr>
</table>

- **GitHub Issues**: [提交问题](https://github.com/tmwgsicp/wechat-download-api/issues)

---

## 致谢

- [FastAPI](https://fastapi.tiangolo.com/) — 高性能 Python Web 框架
- [curl_cffi](https://github.com/lexiforest/curl_cffi) — 支持浏览器 TLS 指纹模拟的 HTTP 客户端
- [HTTPX](https://www.python-httpx.org/) — 现代化 HTTP 客户端
- [gost](https://github.com/go-gost/gost) — 轻量级代理工具

---

<div align="center">

**如果觉得项目有用，请给个 Star 支持一下！**

[![Star History Chart](https://api.star-history.com/svg?repos=tmwgsicp/wechat-download-api&type=Date)](https://star-history.com/#tmwgsicp/wechat-download-api&Date)

Made with ❤️ by [tmwgsicp](https://github.com/tmwgsicp)

</div>
