"""
docker_converter/backup.py
备份 / 恢复功能

备份目录：backups/
- 每份备份为 JSON 文件： backups/YYYYMMDD_HHMMSS.json
- 包含用户列表 + 转换历史
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from .db import ConversionHistory, User, db


# ──────────────────────────────────────────────────────────────
# 路径常量
# ──────────────────────────────────────────────────────────────

def _backup_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bd = os.path.join(root, "backups")
    os.makedirs(bd, exist_ok=True)
    return bd


# ──────────────────────────────────────────────────────────────
# 导出（备份）
# ──────────────────────────────────────────────────────────────

def export_backup() -> str:
    """
    将所有用户和转换历史导出为 JSON 文件。
    Returns 备份文件路径。
    """
    users = []
    for u in User.query.order_by(User.id).all():
        users.append({
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "created_at": u.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            # 不导出密码哈希
        })

    history = [h.to_dict() for h in ConversionHistory.query.order_by(ConversionHistory.id).all()]

    payload = {
        "version": "1.0",
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "users": users,
        "conversion_history": history,
    }

    filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".json"
    filepath = os.path.join(_backup_dir(), filename)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return filepath


def list_backups() -> list[dict]:
    """返回所有备份文件的元信息列表。"""
    bd = _backup_dir()
    files = []
    for name in sorted(os.listdir(bd)):
        if name.endswith(".json"):
            path = os.path.join(bd, name)
            try:
                size = os.path.getsize(path)
                mtime = datetime.fromtimestamp(os.path.getmtime(path))
            except OSError:
                continue  # 跳过无法访问的文件
            files.append({
                "filename": name,
                "size": size,
                "mtime": mtime.strftime("%Y-%m-%d %H:%M:%S"),
            })
    return files


# ──────────────────────────────────────────────────────────────
# 导入（恢复）
# ──────────────────────────────────────────────────────────────

def import_backup(filepath: str) -> tuple[int, int, str]:
    """
    从 JSON 备份文件恢复数据。
    已存在用户（username 相同）跳过，历史记录按原 ID 恢复。

    Returns:
        (users_count, history_count, message)
    """
    with open(filepath, "r", encoding="utf-8") as f:
        payload = json.load(f)

    version = payload.get("version", "unknown")
    imported_users = 0
    imported_history = 0

    # 用户（仅恢复元信息，不恢复密码）
    for u_data in payload.get("users", []):
        existing = User.query.filter_by(username=u_data["username"]).first()
        if not existing:
            user = User(
                username=u_data["username"],
                password_hash=hash_password_fallback(),
                role=u_data.get("role", "user"),
            )
            db.session.add(user)
            imported_users += 1

    # 转换历史（按 ID 恢复，ID 冲突则跳过）
    for h_data in payload.get("conversion_history", []):
        existing = db.session.get(ConversionHistory, h_data["id"])
        if existing:
            continue
        # 查找对应用户名
        username = h_data.get("username", "unknown")
        user = User.query.filter_by(username=username).first()
        if not user:
            continue  # 用户不存在则跳过
        hist = ConversionHistory(
            id=h_data["id"],
            user_id=user.id,
            input_text=h_data["input_text"],
            output_yaml=h_data["output_yaml"],
            success_count=h_data.get("success_count", 0),
            failed_count=h_data.get("failed_count", 0),
        )
        db.session.add(hist)
        imported_history += 1

    db.session.commit()
    return imported_users, imported_history, f"v{version} 导入完成"


def hash_password_fallback() -> str:
    """占位密码哈希（导入用户无法登录，除非重新设置密码）。"""
    import bcrypt as _bcrypt
    return _bcrypt.hashpw(b"__imported__", _bcrypt.gensalt()).decode("utf-8")
