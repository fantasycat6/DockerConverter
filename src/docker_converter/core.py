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
    "domainname",
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
    "working_dir",
    "cap_add",
    "cap_drop",
    "devices",
    "labels",
    "sysctls",
    "tmpfs",
    "dns",
    "dns_search",
    "dns_opt",
    "ipc",
    "pid",
    "uts",
    "userns_mode",
    "isolation",
    "init",
    "network_mode",
    "network_aliases",
    "links",
    "group_add",
    "mac_address",
    "ip",
    "ip6",
    "shm_size",
    "cgroup_parent",
    "cgroup",
    "ulimits",
    "security_opt",
    "storage_opt",
    "runtime",
    "platform",
    "stop_signal",
    "stop_grace_period",
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

# 新增更多参数的示例
docker run -d \\
  --name my-full-service \\
  -p 8081:80 \\
  -v ./data:/data \\
  -e APP_ENV=prod \\
  -e DB_URL=postgres://user:pass@db:5432/mydb \\
  --hostname myapp \\
  --domainname example.com \\
  --workdir /app \\
  --dns 8.8.8.8 \\
  --dns 8.8.4.4 \\
  --dns-search localdomain \\
  --dns-option ndots:0 \\
  --ipc host \\
  --pid host \\
  --uts host \\
  --init \\
  --network my-net \\
  --network-alias app1 \\
  --network-alias app2 \\
  --link my-db:db \\
  --group-add 999 \\
  --mac-address 02:42:ac:11:00:02 \\
  --ip 172.20.0.10 \\
  --shm-size 2g \\
  --privileged \\
  --read-only \\
  -it \\
  --user 1000:1000 \\
  --cap-add SYS_ADMIN \\
  --cap-drop MKNOD \\
  --device /dev/ttyUSB0 \\
  --label app=myapp \\
  --label version=1.0 \\
  --sysctl net.core.somaxconn=1024 \\
  --tmpfs /tmp:rw,size=100m \\
  --ulimit nofile=65536:65536 \\
  --ulimit nproc=4096 \\
  --security-opt apparmor=unconfined \\
  --stop-signal SIGINT \\
  --stop-timeout 60 \\
  --restart always \\
  --health-cmd "curl -f http://localhost/health || exit 1" \\
  --health-interval 30s \\
  --health-timeout 10s \\
  --health-retries 3 \\
  --health-start-period 1m \\
  --gpus all \\
  --memory 1g \\
  --memory-reservation 512m \\
  --memory-swap 2g \\
  --cpu-shares 512 \\
  --cpuset-cpus 0-1 \\
  --oom-kill-disable \\
  --platform linux/amd64 \\
  nginx:alpine
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


def _parse_kv_pair(kv_str: str) -> dict:
    """解析 'key=value' 格式的字符串为字典。"""
    if "=" in kv_str:
        key, val = kv_str.split("=", 1)
        return {key.strip(): val.strip()}
    return {}


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

        elif arg in ("-w", "--workdir"):
            service_config["working_dir"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--domainname":
            service_config["domainname"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--dns":
            service_config.setdefault("dns", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg == "--dns-option":
            service_config.setdefault("dns_opt", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg == "--dns-search":
            service_config.setdefault("dns_search", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg == "--ipc":
            service_config["ipc"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--pid":
            service_config["pid"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--uts":
            service_config["uts"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--userns":
            service_config["userns_mode"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--isolation":
            service_config["isolation"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--init":
            service_config["init"] = True
            i += 1

        elif arg == "--network-alias":
            service_config.setdefault("network_aliases", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg == "--link":
            service_config.setdefault("links", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg == "--group-add":
            service_config.setdefault("group_add", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg == "--mac-address":
            service_config["mac_address"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--ip":
            service_config["ip"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--ip6":
            service_config["ip6"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--shm-size":
            service_config["shm_size"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--cgroup-parent":
            service_config["cgroup_parent"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--cgroupns":
            service_config["cgroup"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--security-opt":
            service_config.setdefault("security_opt", []).append(_safe_next(args, i, arg))
            i += 2

        elif arg == "--storage-opt":
            service_config.setdefault("storage_opt", {}).update(_parse_kv_pair(_safe_next(args, i, arg)))
            i += 2

        elif arg == "--runtime":
            service_config["runtime"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--platform":
            service_config["platform"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--stop-signal":
            service_config["stop_signal"] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--stop-timeout":
            timeout = _safe_next(args, i, arg)
            service_config["stop_grace_period"] = f"{timeout}s"
            i += 2

        elif arg in ("--ulimit", "--ulimits"):
            ulimit_val = _safe_next(args, i, arg)
            if "=" in ulimit_val:
                name, limit = ulimit_val.split("=", 1)
                if ":" in limit:
                    soft, hard = limit.split(":", 1)
                    service_config.setdefault("ulimits", {})[name] = {"soft": soft, "hard": hard}
                else:
                    service_config.setdefault("ulimits", {})[name] = limit
            i += 2

        elif arg in ("-m", "--memory", "--memory-reservation", "--memory-swap", "--memory-swappiness", "--cpu-shares", "--cpuset-cpus", "--cpuset-mems", "--cpu-period", "--cpu-quota", "--cpu-rt-period", "--cpu-rt-runtime", "--pids-limit", "--oom-kill-disable", "--oom-score-adj", "--blkio-weight", "--blkio-weight-device", "--device-read-bps", "--device-write-bps", "--device-read-iops", "--device-write-iops", "--device-cgroup-rule"):
            key_map = {
                "--memory": "memory",
                "--memory-reservation": "memory_reservation",
                "--memory-swap": "memory_swap",
                "--memory-swappiness": "memory_swappiness",
                "--cpu-shares": "cpu_shares",
                "--cpuset-cpus": "cpuset_cpus",
                "--cpuset-mems": "cpuset_mems",
                "--cpu-period": "cpu_period",
                "--cpu-quota": "cpu_quota",
                "--cpu-rt-period": "cpu_rt_period",
                "--cpu-rt-runtime": "cpu_rt_runtime",
                "--pids-limit": "pids_limit",
                "--oom-score-adj": "oom_score_adj",
                "--blkio-weight": "blkio_weight",
                "--blkio-weight-device": "blkio_weight_device",
                "--device-read-bps": "device_read_bps",
                "--device-write-bps": "device_write_bps",
                "--device-read-iops": "device_read_iops",
                "--device-write-iops": "device_write_iops",
                "--device-cgroup-rule": "device_cgroup_rule",
            }
            if arg == "--oom-kill-disable":
                (service_config
                 .setdefault("deploy", {})
                 .setdefault("resources", {})
                 .setdefault("limits", {}))["oom_kill_disable"] = True
                i += 1
            else:
                if arg in ("--blkio-weight-device", "--device-read-bps", "--device-write-bps", "--device-read-iops", "--device-write-iops", "--device-cgroup-rule"):
                    (service_config
                     .setdefault("deploy", {})
                     .setdefault("resources", {})
                     .setdefault("limits", {})
                     .setdefault(key_map[arg], [])).append(_safe_next(args, i, arg))
                else:
                    (service_config
                     .setdefault("deploy", {})
                     .setdefault("resources", {})
                     .setdefault("limits", {}))[key_map[arg]] = _safe_next(args, i, arg)
                i += 2

        elif arg in ("--cpus",):
            key_map = {"--cpus": "cpus"}
            (service_config
             .setdefault("deploy", {})
             .setdefault("resources", {})
             .setdefault("limits", {}))[key_map[arg]] = _safe_next(args, i, arg)
            i += 2

        elif arg == "--gpus":
            (service_config
             .setdefault("deploy", {})
             .setdefault("resources", {})
             .setdefault("reservations", {})
             .setdefault("devices", []))
            gpu_val = _safe_next(args, i, arg)
            if gpu_val == "all":
                service_config["deploy"]["resources"]["reservations"]["devices"].append({
                    "driver": "nvidia",
                    "count": -1,
                    "capabilities": [["gpu"]]
                })
            else:
                try:
                    count = int(gpu_val)
                    service_config["deploy"]["resources"]["reservations"]["devices"].append({
                        "driver": "nvidia",
                        "count": count,
                        "capabilities": [["gpu"]]
                    })
                except ValueError:
                    log.warn(f"Unsupported --gpus value '{gpu_val}', using all GPUs")
                    service_config["deploy"]["resources"]["reservations"]["devices"].append({
                        "driver": "nvidia",
                        "count": -1,
                        "capabilities": [["gpu"]]
                    })
            i += 2

        elif arg.startswith("--health-"):
            if arg == "--health-cmd":
                service_config.setdefault("healthcheck", {})["test"] = _safe_next(args, i, arg).split()
                i += 2
            elif arg == "--health-interval":
                service_config.setdefault("healthcheck", {})["interval"] = _safe_next(args, i, arg)
                i += 2
            elif arg == "--health-timeout":
                service_config.setdefault("healthcheck", {})["timeout"] = _safe_next(args, i, arg)
                i += 2
            elif arg == "--health-retries":
                service_config.setdefault("healthcheck", {})["retries"] = int(_safe_next(args, i, arg))
                i += 2
            elif arg == "--health-start-period":
                service_config.setdefault("healthcheck", {})["start_period"] = _safe_next(args, i, arg)
                i += 2
            elif arg == "--health-start-interval":
                log.warn(f"Healthcheck option '{arg}' is not supported and will be skipped.")
                if i + 1 < len(args) and not args[i + 1].startswith("-"):
                    i += 2
                else:
                    i += 1

        elif arg == "--rm":
            log.warn("Option '--rm' is not applicable to Docker Compose (container removal on exit is not supported in Compose).")
            log.info("In Docker Compose, containers are not automatically removed on exit by default.")
            i += 1
        
        elif arg in ("-d", "--detach", "--no-healthcheck", "--pull", "-q", "--quiet", "--cidfile", "--use-api-socket", "--attach", "--annotation", "--label-file", "--volume-driver", "--volumes-from", "--log-driver", "--log-opt", "--link-local-ip", "--sig-proxy", "--detach-keys", "--mount"):
            log.warn(f"Option '{arg}' is not fully supported and will be ignored.")
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                i += 2
            else:
                i += 1

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
