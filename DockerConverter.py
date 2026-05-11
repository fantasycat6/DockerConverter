"""
DockerConverter.py — CLI 入口
将 docker_commands.txt 中的命令转换为 docker-compose.yml。

用法：
    python DockerConverter.py [input] [output]

    input   输入文件路径（默认：docker_commands.txt）
    output  输出文件路径（默认：docker-compose.yml）
"""

from __future__ import annotations

import os
import sys

# 将项目根目录加入 Python 路径，确保能 import src 包
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.docker_converter.core import convert_commands_to_yaml  # noqa: E402


# ──────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]
    input_file  = args[0] if len(args) > 0 else os.path.join("samples", "docker_commands.txt")
    output_file = args[1] if len(args) > 1 else "docker-compose.yml"

    # 读取输入文件（默认从 samples/ 目录）
    if not os.path.exists(input_file):
        print(f"  [ERROR] Input file not found: '{input_file}'", file=sys.stderr)
        print(f"  [INFO]  Usage: python DockerConverter.py [input.txt] [output.yml]", file=sys.stderr)
        sys.exit(1)

    print(f"  [INFO]  Reading: {input_file}")

    # 读取输入文件
    try:
        with open(input_file, "r", encoding="utf-8") as f:
            commands_text = f.read()
    except OSError as e:
        print(f"  [ERROR] Cannot read '{input_file}': {e}", file=sys.stderr)
        sys.exit(1)

    # 执行转换
    result = convert_commands_to_yaml(commands_text)

    # 打印日志
    sep = "─" * 60
    print("═" * 60)
    print(sep)
    for entry in result["logs"]:
        lvl = entry["level"].upper().center(5)
        print(f"  [{lvl}]  {entry['message']}")
    print(sep)
    print("═" * 60)

    if not result["yaml"]:
        print("\n  Conversion failed. Please check the error messages above.")
        sys.exit(1)

    # 写出 YAML
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(result["yaml"])
    except OSError as e:
        print(f"  [ERROR] Cannot write '{output_file}': {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n  [ OK ]  Generated: {os.path.abspath(output_file)}")
    print(f"  Check '{output_file}' for the generated docker-compose configuration.")


if __name__ == "__main__":
    main()
