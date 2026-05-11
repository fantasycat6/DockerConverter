"""
docker_converter/db.py
数据库初始化与模型定义（SQLite + Flask-SQLAlchemy）

数据文件：data/converter.db
"""

from __future__ import annotations

import os
from datetime import datetime

from flask_login import AnonymousUserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


# ──────────────────────────────────────────────────────────────
# Anonymous user (Flask-Login required)
# ──────────────────────────────────────────────────────────────

class _AnonymousUser(AnonymousUserMixin):
    @property
    def is_admin(self) -> bool:
        return False

    @property
    def is_active(self) -> bool:
        return False

    @property
    def is_authenticated(self) -> bool:
        return False


# ──────────────────────────────────────────────────────────────
# 用户模型
# ──────────────────────────────────────────────────────────────

class User(db.Model):
    """系统用户。role='admin' | 'user'"""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")  # admin / user
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # 关联：此用户的转换记录
    conversions = db.relationship(
        "ConversionHistory",
        backref="user",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_active(self) -> bool:
        """Flask-Login required property."""
        return True

    @property
    def is_authenticated(self) -> bool:
        """Flask-Login required property."""
        return True

    def get_id(self) -> str:
        """Flask-Login required method."""
        return str(self.id)

    def __repr__(self) -> str:
        return f"<User {self.username} [{self.role}]>"


# ──────────────────────────────────────────────────────────────
# 转换历史
# ──────────────────────────────────────────────────────────────

class ConversionHistory(db.Model):
    """每次转换操作的记录。"""

    __tablename__ = "conversion_history"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    input_text = db.Column(db.Text, nullable=False)     # 原始命令文本
    output_yaml = db.Column(db.Text, nullable=False)    # 生成的 YAML
    success_count = db.Column(db.Integer, default=0)
    failed_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.user.username if self.user else "?",
            "input_text": self.input_text,
            "output_yaml": self.output_yaml,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def __repr__(self) -> str:
        return f"<ConversionHistory #{self.id} by user={self.user_id}>"
