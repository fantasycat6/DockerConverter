# DockerConverter

将 `docker run` 命令批量转换为 `docker-compose.yml` 文件。
支持 **Web UI** 和 **命令行（CLI）** 两种使用方式。

> 当前版本：**v2.2**

---

## 目录结构

```
DockerConverter/
├── src/
│   └── docker_converter/
│       ├── __init__.py      # 包版本
│       ├── core.py          # 核心转换逻辑
│       ├── app.py           # Flask Web 服务入口
│       ├── auth.py          # 用户认证与权限
│       ├── db.py            # 数据库模型（SQLAlchemy）
│       └── backup.py        # 备份导出 / 导入
├── templates/
│   ├── index.html            # 转换工具主界面
│   ├── login.html            # 登录页
│   ├── register.html         # 注册页
│   ├── admin.html            # 管理面板
│   ├── profile.html          # 用户中心
│   ├── history_detail.html   # 转换历史详情
│   └── error.html            # 错误页
├── samples/
│   └── docker_commands.txt   # 示例命令文件
├── .env                      # 环境变量配置（不提交）
├── .env.example              # 环境变量示例
├── .gitignore
├── DockerConverter.py        # CLI 入口
├── RunWeb.bat               # 启动 Web UI（Windows 双击）
├── RunCLI.bat               # 启动 CLI（Windows 双击）
├── requirements.txt
└── README.md
```

---

## 快速开始

### 方式一：Web UI（推荐）

**1. 安装依赖**
```bash
pip install -r requirements.txt
```

**2. 启动服务（Windows 双击）**
- `RunWeb.bat` — 启动 Web UI

**2. 启动服务（命令行）**
```bash
python -m src.docker_converter.app
```

**3. 打开浏览器**
```
http://127.0.0.1:5030
```
首次访问会自动跳转注册页面，第一个注册用户为管理员。

**Web UI 功能：**
- 左侧粘贴 `docker run` 命令（支持多条、多行续行 `\`、注释 `#`）
- 上传 `.txt` 文件批量处理
- 右侧实时预览生成的 YAML，支持语法高亮
- 一键复制或下载 `docker-compose.yml`
- 转换历史自动保存，可查看详情、分页浏览
- 管理面板：用户管理、转换历史、备份 / 恢复
- 用户中心：修改密码、查看个人转换统计

---

### 方式二：命令行（CLI）

**Windows 双击：** `RunCLI.bat`

**直接运行：**
```bash
# 使用默认文件（samples/docker_commands.txt → docker-compose.yml）
python DockerConverter.py

# 指定输入输出文件
python DockerConverter.py my_commands.txt my-compose.yml
```

---

## 环境变量配置

复制 `.env.example` 为 `.env` 后修改：

```env
PORT=5030
HOST=127.0.0.1

# Flask SECRET_KEY（生产环境务必修改！）
SECRET_KEY=your-secret-key-here

# 默认管理员（首次启动时自动创建，仅当数据库为空时生效）
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin123
```

---

## 输入文件格式

```text
# 注释行，会被忽略

docker run -d \
  --name my-nginx \
  -p 80:80 \
  -v ./html:/usr/share/nginx/html:ro \
  --restart unless-stopped \
  nginx:latest

docker run --name my-db -p 5432:5432 \
  -e POSTGRES_PASSWORD=secret \
  --network app-net \
  --restart always \
  postgres:13
```

---

## 支持的 `docker run` 参数

| docker run 参数 | docker-compose 字段 |
|---|---|
| `--name` | `container_name` + 服务名 |
| `-p` / `--publish` | `ports` |
| `-v` / `--volume` | `volumes` |
| `-e` / `--env` | `environment` |
| `--restart` | `restart` |
| `--network` | `networks`（顶层声明为 `external: true`） |
| `--hostname` | `hostname` |
| `--entrypoint` | `entrypoint` |
| `--add-host` | `extra_hosts` |
| `--env-file` | `env_file` |
| `--expose` | `expose` |
| `--privileged` | `privileged: true` |
| `--read-only` | `read_only: true` |
| `-t` / `--tty` | `tty: true` |
| `-i` / `--interactive` | `stdin_open: true` |
| `--user` | `user` |
| `--cap-add` / `--cap-drop` | `cap_add` / `cap_drop` |
| `--device` | `devices` |
| `--label` | `labels` |
| `--sysctl` | `sysctls` |
| `--tmpfs` | `tmpfs` |
| `--cpus` / `--memory` | `deploy.resources.limits` |
| `-d` / `--detach` / `--rm` | 忽略（compose 默认行为） |

---

## 管理面板功能

- **转换历史**：查看所有用户的转换记录，支持详情页、多选批量删除、分页跳转
- **用户管理**：管理用户账号，修改密码（管理员改密需验证旧密码）
- **备份恢复**：全量备份导出 / 导入，支持下载和恢复
- **用户中心**：修改个人密码

---

## 注意事项

- `--network` 指定的网络默认声明为 `external: true`，如需内部网络请手动修改
- `--health-*` healthcheck 参数目前仅打印警告，不解析
- `--cpus` / `--memory` 映射到 compose v3 的 `deploy.resources.limits`
- 首位注册用户自动成为管理员（如配置了 `ADMIN_USERNAME` 则优先使用该账号）

---

## 许可证

MIT License
