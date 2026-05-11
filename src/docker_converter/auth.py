"""
docker_converter/auth.py
用户认证逻辑（Flask-Login + bcrypt）

- 首位注册用户自动成为管理员
- 密码使用 bcrypt 哈希存储
"""

from __future__ import annotations

from functools import wraps
from typing import Callable

import bcrypt
from flask import jsonify
from flask_login import LoginManager, current_user, login_required, login_user, logout_user  # noqa: F401

from .db import User, db


# ──────────────────────────────────────────────────────────────
# Flask-Login 初始化（由 app.py 调用）
# ──────────────────────────────────────────────────────────────

login_manager = LoginManager()


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return db.session.get(User, int(user_id))


@login_manager.unauthorized_handler
def unauthorized():
    return jsonify({"error": "请先登录"}), 401


# ──────────────────────────────────────────────────────────────
# 密码工具
# ──────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


# ──────────────────────────────────────────────────────────────
# 用户管理函数
# ──────────────────────────────────────────────────────────────

def create_user(username: str, password: str) -> tuple[User, str]:
    """
    创建新用户。首位用户自动设为管理员。

    Returns:
        (user, message)
    """
    existing = User.query.filter_by(username=username).first()
    if existing:
        return None, "用户名已存在"

    # 首位注册用户 → 管理员
    is_first = User.query.count() == 0
    role = "admin" if is_first else "user"

    user = User(
        username=username.strip(),
        password_hash=hash_password(password),
        role=role,
    )
    db.session.add(user)
    db.session.commit()
    return user, f"创建成功，角色：{role}"


def authenticate_user(username: str, password: str) -> tuple[User | None, str]:
    """
    验证用户登录。

    Returns:
        (user, message)
    """
    user = User.query.filter_by(username=username).first()
    if not user:
        return None, "用户名或密码错误"

    if not verify_password(password, user.password_hash):
        return None, "用户名或密码错误"

    return user, "登录成功"


def delete_user_by_id(user_id: int) -> bool:
    """删除指定 ID 的用户（不能删除自己）。"""
    from flask_login import current_user
    if current_user.is_authenticated and current_user.id == user_id:
        return False  # 不能删除自己
    user = db.session.get(User, user_id)
    if not user:
        return False
    db.session.delete(user)
    db.session.commit()
    return True


# ──────────────────────────────────────────────────────────────
# 权限装饰器
# ──────────────────────────────────────────────────────────────

def admin_required(f: Callable):
    """要求管理员权限的装饰器。"""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        from flask_login import current_user
        if not current_user.is_admin:
            return jsonify({"error": "需要管理员权限"}), 403
        return f(*args, **kwargs)
    return decorated
