"""
docker_converter/core.py
核心转换逻辑：docker run 命令 → docker-compose.yml

公共 API：
    parse_docker_run_command(cmd, base_name)  -> dict
    convert_commands_to_yaml(text)            -> dict  {"yaml": str, "logs": list}
"""

from __future__ import annotations

import io
import re
import shlex
from typing import Optional

import yaml


# ──────────────────────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────────────────────

# docker-compose 字段优先顺序（其余字段追加到末尾）
FIELD_ORDER: list[str] = [
    "image",
    "container_name",
    "ports",
    "environment",
    "volumes",
    "restart",
    "networks",
    "hostname",
    "entrypoint",
    "command",
    "env_file",
    "expose",
    "extra_hosts",
    "privileged",
    "read_only",
    "tty",
    "stdin_open",
    "user",
    "cap_add",
    "cap_drop",
    "devices",
    "labels",
    "sysctls",
    "tmpfs",
    "deploy",
]

# 示例命令（当 docker_commands.txt 不存在时写入）
SAMPLE_COMMANDS: str = """\
docker run -d \\
  --name my-nginx \\
  -p 80:80 \\
  -v ./nginx_html:/usr/share/nginx/html:ro \\
  --restart unless-stopped \\
  nginx:latest

docker run --name my-backend -p 8080:8080 -e DB_HOST=database \\
  -v ./logs:/app/logs --restart on-failure myorg/my-backend:v1.0

docker run -d --name my-db -p 5432:5432 \\
  -e POSTGRES_PASSWORD=mysecretpassword \\
  --network app-tier \\
  --restart always \\
  postgres:13

# 这是注释，会被忽略

docker run --name my-worker \\
  --network app-tier \\
  --add-host host.docker.internal:host-gateway \\
  myorg/worker:v2.1

docker run --name my-other-backend -p 9000:9000 myorg/my-backend:v1.0

docker run --name another-service \\
  --network another-net \\
  alpine echo hello world

docker run --name another-app \\
  --cpus 0.25 \\
  --memory 64m \\
  myapp:dev
"""


# ──────────────────────────────────────────────────────────────
# 内部日志收集器
# ──────────────────────────────────────────────────────────────

class _Logger:
    """轻量日志收集器，结果可序列化为 JSON。"""

    def __init__(self) -> None:
        self._logs: list[dict] = []

    def info(self, msg: str) -> None:
        self._logs.append({"level": "info", "message": msg})

    def ok(self, msg: str) -> None:
        self._logs.append({"level": "ok", "message": msg})

    def warn(self, msg: str) -> None:
        self._logs.append({"level": "warn", "message": msg})

    def error(self, msg: str) -> None:
        self._logs.append({"level": "error", "message": msg})

    @property
    def logs(self) -> list[dict]:
        return list(self._logs)


# ──────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────

def _safe_next(args: list[str], index: int, flag: str) -> str:
    """安全获取下一个参数值，越界时抛出清晰的 ValueError。"""
    if index + 1 >= len(args):
        raise ValueError(f"Flag '{flag}' requires a value but none was provided.")
    return args[index + 1]


def _slugify(raw: str) -> str:
    """将镜像名转换为合法的 compose 服务名。"""
    name = raw.split("/")[-1].split(":")[0]
    name = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    return name or "service"


def _reorder(config: dict) -> dict:
    """按 FIELD_ORDER 对服务配置字段排序，未列出字段追加到末尾。"""
    ordered: dict = {}
    for key in FIELD_ORDER:
        if key in config:
            ordered[key] = config[key]
    for key, value in config.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def _unique_name(name: str, existing: dict) -> str:
    """若名称已存在，追加数字后缀直到唯一。"""
    if name not in existing:
        return name
    suffix = 2
    while f"{name}_{suffix}" in existing:
        suffix += 1
    return f"{name}_{suffix}"


def _flush_buffer(buffer: list[str]) -> str:
    """将多行缓冲区合并为单行，移除续行反斜杠。"""
    raw = " ".join(buffer)
    raw = re.sub(r"\s*\\\s*", " ", raw)
    return raw.strip()


# ──────────────────────────────────────────────────────────────
# 核心解析：单条 docker run 命令
# ──────────────────────────────────────────────────────────────

def parse_docker_run_command(
    docker_run_command: str,
    service_base_name: str,
    logger: Optional[_Logger] = None,
) -> dict:
    """
    解析单条 docker run 命令，返回结构化字典。

    Args:
        docker_run_command: 已合并为单行的完整命令字符串。
        service_base_name:  无法推断名称时的备用服务名。
        logger:             可选的日志收集器（警告会写入）。

    Returns:
        {
            "service_name":   str,
            "service_config": dict,   # 已按 FIELD_ORDER 排序
            "networks_defined": list[str],
        }

    Raises:
        ValueError: 命令格式非法或缺少镜像名。
    """
    log = logger or _Logger()

    try:
        args = shlex.split(docker_run_command)
    except ValueError as exc:
        raise ValueError(f"Failed to tokenize command: {exc}") from exc

    if len(args) < 2 or args[0] != "docker" or args[1] != "run":
        raise ValueError(
            f"Command must start with 'docker run'. Got: '{docker_run_command[:80]}'"
        )

    args = args[2:]

    service_name: str = service_base_name
    service_config: dict = {}
    image_name: Optional[str] = None
    command_args: list[str] = []
    networks_defined: list[str] = []
    name_set = False

    i = 0
    while i < len(args):
        arg = args[i]

        if arg == "--name":
            val = _safe_next(args, i, arg)
            service_name = val
            service_config["container_name"] = val
            name_set = True
            i += 2

        elif arg in ("-p", "--publish"):
            service_config.setdefault("ports", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg in ("-v", "--volume"):
            service_config.setdefault("volumes", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg in ("-e", "--env"):
            service_config.setdefault("environment", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg == "--restart":
            service_config["restart"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--network":
            net = _safe_next(args, i, arg)
            if net not in networks_defined:
                networks_defined.append(net)
            i += 2

        elif arg == "--hostname":
            service_config["hostname"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--entrypoint":
            service_config["entrypoint"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--add-host":
            service_config.setdefault("extra_hosts", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg == "--env-file":
            service_config.setdefault("env_file", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg == "--expose":
            service_config.setdefault("expose", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg == "--privileged":
            service_config["privileged"] = True
            i += 1

        elif arg == "--read-only":
            service_config["read_only"] = True
            i += 1

        elif arg in ("-t", "--tty"):
            service_config["tty"] = True
            i += 1

        elif arg in ("-i", "--interactive"):
            service_config["stdin_open"] = True
            i += 1

        elif arg == "--user":
            service_config["user"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--cap-add":
            service_config.setdefault("cap_add", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg == "--cap-drop":
            service_config.setdefault("cap_drop", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg == "--device":
            service_config.setdefault("devices", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg == "--label":
            service_config.setdefault("labels", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg == "--sysctl":
            service_config.setdefault("sysctls", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg == "--tmpfs":
            service_config.setdefault("tmpfs", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg in ("--cpus", "--memory", "--memory-swap"):
            key_map = {"--cpus": "cpus", "--memory": "memory", "--memory-swap": "memory_swap"}
            (service_config
             .setdefault("deploy", {})
             .setdefault("resources", {})
             .setdefault("limits", {}))[key_map[arg]] = _safe_next(args, i, arg)
            i += 2

        elif arg.startswith("--health-"):
            log.warn(f"Healthcheck option '{arg}' is not supported and will be skipped.")
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                i += 2
            else:
                i += 1

        elif arg in ("-d", "--detach", "--rm", "--no-healthcheck"):
            i += 1  # 静默忽略

        elif arg.startswith("-"):
            log.warn(f"Unsupported option '{arg}' will be ignored.")
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                i += 2
            else:
                i += 1

        else:
            if image_name is None:
                image_name = arg
            else:
                command_args.append(arg)
            i += 1

    if not image_name:
        raise ValueError(
            f"No image name found in command: '{docker_run_command[:80]}'"
        )

    service_config["image"] = image_name

    if not name_set:
        service_name = _slugify(image_name) or service_base_name

    if command_args:
        service_config["command"] = command_args if len(command_args) > 1 else command_args[0]

    return {
        "service_name": service_name,
        "service_config": _reorder(service_config),
        "networks_defined": networks_defined,
    }


# ──────────────────────────────────────────────────────────────
# 高阶函数：多行文本 → YAML 字符串
# ──────────────────────────────────────────────────────────────

def convert_commands_to_yaml(commands_text: str) -> dict:
    """
    将包含多条 docker run 命令的字符串转换为 docker-compose YAML 字符串。

    Args:
        commands_text: 原始命令文本（可含多行续行、注释行、空行）。

    Returns:
        {
            "yaml":  str,          # 生成的 YAML 字符串；失败时为空字符串
            "logs":  list[dict],   # [{"level": "ok"|"info"|"warn"|"error", "message": str}]
            "success": int,        # 成功解析服务数
            "failed":  int,        # 解析失败命令数
        }
    """
    log = _Logger()
    all_services: dict = {}
    all_networks: dict = {}
    service_counter = 0
    success_count = 0
    fail_count = 0

    lines = commands_text.splitlines()
    buffer: list[str] = []
    buffer_start_line = 0

    def _flush(start_line: int) -> None:
        nonlocal service_counter, success_count, fail_count
        if not buffer:
            return
        full_cmd = _flush_buffer(buffer)
        buffer.clear()
        service_counter += 1
        default_name = f"service_{service_counter}"

        try:
            parsed = parse_docker_run_command(full_cmd, default_name, log)
        except ValueError as exc:
            log.error(f"Line {start_line}: {exc} — skipped.")
            fail_count += 1
            return
        except Exception as exc:  # noqa: BLE001
            log.error(f"Line {start_line}: Unexpected error — {exc} — skipped.")
            fail_count += 1
            return

        svc_name = _unique_name(parsed["service_name"], all_services)
        svc_cfg  = parsed["service_config"]
        nets     = parsed["networks_defined"]

        all_services[svc_name] = svc_cfg
        if nets:
            svc_cfg["networks"] = nets
            for net in nets:
                all_networks.setdefault(net, {"external": True})

        log.ok(f"Line {start_line:>4} → service '{svc_name}'  ({svc_cfg.get('image', '?')})")
        success_count += 1

    for line_num, raw_line in enumerate(lines, 1):
        line = raw_line.strip()

        if not line or line.startswith("#"):
            if buffer:
                _flush(buffer_start_line)
            continue

        is_new_cmd = line.startswith("docker run")

        if is_new_cmd and buffer:
            _flush(buffer_start_line)

        if is_new_cmd and not buffer:
            buffer_start_line = line_num

        buffer.append(line)

        if not line.endswith("\\") and buffer:
            _flush(buffer_start_line)

    if buffer:
        _flush(buffer_start_line)

    log.info(f"Parsed: {success_count} succeeded, {fail_count} failed.")

    if not all_services:
        log.warn("No valid services found. Nothing to output.")
        return {"yaml": "", "logs": log.logs, "success": 0, "failed": fail_count}

    compose_data: dict = {"services": all_services}
    if all_networks:
        compose_data["networks"] = all_networks

    stream = io.StringIO()
    yaml.dump(
        compose_data,
        stream,
        sort_keys=False,
        indent=2,
        default_flow_style=False,
        allow_unicode=True,
    )
    yaml_text = stream.getvalue()

    return {
        "yaml":    yaml_text,
        "logs":    log.logs,
        "success": success_count,
        "failed":  fail_count,
    }
