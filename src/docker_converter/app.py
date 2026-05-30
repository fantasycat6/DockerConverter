"""
docker_converter/app.py
Flask Web 服务入口（含用户认证、转换、备份/恢复）

路由：
    GET  /              → 主界面
    GET  /login         → 登录页
    GET  /register      → 注册页
    GET  /admin         → 管理面板（需管理员）
    POST /api/login     → 登录
    POST /api/register  → 注册
    POST /api/logout    → 登出
    GET  /api/me        → 当前用户信息
    POST /api/convert   → JSON {"text":"..."} → {"yaml":..., "logs":[...]}
    POST /api/upload    → multipart .txt 上传
    GET  /api/history   → 转换历史（需登录）
    DELETE /api/history/<id> → 删除单条历史（需登录）
    GET  /api/admin/users   → 用户列表（需管理员）
    DELETE /api/admin/users/<id> → 删除用户（需管理员）
    GET  /api/backup/export  → 导出备份
    POST /api/backup/import  → 导入备份（multipart JSON）
    GET  /api/backup/list    → 备份文件列表
    GET  /favicon.ico        → SVG favicon
"""

from __future__ import annotations

import io
import os
import sys
from functools import wraps

from dotenv import load_dotenv

# 加载 .env 环境变量
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_ROOT, ".env"))

from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user

# 允许从项目根目录直接运行
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.docker_converter.auth import (  # noqa: E402
    admin_required,
    authenticate_user,
    create_user,
    delete_user_by_id,
    hash_password,
    verify_password,
    login_manager,
    login_required,
    login_user,
    logout_user,
)
from src.docker_converter.backup import (  # noqa: E402
    export_backup,
    import_backup,
    list_backups,
)
from src.docker_converter.core import SAMPLE_COMMANDS, convert_commands_to_yaml  # noqa: E402


# ──────────────────────────────────────────────────────────────
# 自定义登录检查（区分浏览器直接访问 vs AJAX 请求）
# 浏览器访问 → 重定向到 /login；AJAX 请求 → 401 JSON
# ──────────────────────────────────────────────────────────────

def login_required_web(f):
    """适用于页面路由的登录检查。"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.is_authenticated:
            return f(*args, **kwargs)
        # AJAX 请求返回 401 JSON，前端 JS 负责跳转
        if (request.is_json or
                request.headers.get("X-Requested-With") == "XMLHttpRequest"):
            return jsonify({"error": "请先登录", "redirect": "/login"}), 401
        # 浏览器直接访问 → 重定向到登录页
        return redirect(url_for("login_page", next=request.full_path))
    return decorated
from src.docker_converter.db import ConversionHistory, User, db, _AnonymousUser  # noqa: E402

# ──────────────────────────────────────────────────────────────
# Flask 配置
# ──────────────────────────────────────────────────────────────

_TEMPLATE_DIR = os.path.join(_ROOT, "templates")
_DATA_DIR = os.path.join(_ROOT, "data")
os.makedirs(_DATA_DIR, exist_ok=True)

app = Flask(__name__, template_folder=_TEMPLATE_DIR)
_secret = os.environ.get("SECRET_KEY", "")
if not _secret:
    import secrets
    _secret = secrets.token_hex(32)
app.config["SECRET_KEY"] = _secret
_db_uri = os.environ.get("DATABASE_URI", f"sqlite:///{os.path.join(_DATA_DIR, 'converter.db')}")
app.config["SQLALCHEMY_DATABASE_URI"] = _db_uri
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024  # 4 MB

# 初始化扩展
db.init_app(app)
login_manager.init_app(app)
login_manager.login_view = "login_page"
login_manager.anonymous_user = _AnonymousUser

with app.app_context():
    db.create_all()

    # 自动创建默认管理员（仅当数据库为空时）
    if User.query.count() == 0:
        _admin_user = os.environ.get("ADMIN_USERNAME", "admin").strip()
        _admin_pass = os.environ.get("ADMIN_PASSWORD", "admin123")
        if _admin_user and _admin_pass:
            from .auth import create_user
            user, _ = create_user(_admin_user, _admin_pass)
            if user:
                print(f"  [Bootstrap] Default admin created: {_admin_user}")


# ──────────────────────────────────────────────────────────────
# 页面路由
# ──────────────────────────────────────────────────────────────

@app.get("/")
@login_required_web
def index():
    return render_template("index.html", sample=SAMPLE_COMMANDS)


@app.get("/login")
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    return render_template("login.html")


@app.get("/register")
def register_page():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    return render_template("register.html")


@app.get("/admin")
@login_required_web
@admin_required
def admin_page():
    return render_template("admin.html")


@app.get("/history/<int:hist_id>")
@login_required_web
def history_detail_page(hist_id):
    """转换历史详情页（管理员可查看所有，普通用户只能查看自己的）。"""
    hist = db.session.get(ConversionHistory, hist_id)
    if not hist:
        return render_template("error.html", message="记录不存在", code=404), 404
    if not current_user.is_admin and hist.user_id != current_user.id:
        return render_template("error.html", message="无权访问此记录", code=403), 403
    return render_template("history_detail.html", hist=hist)


@app.get("/profile")
@login_required_web
def profile_page():
    """用户中心页面。"""
    return render_template("profile.html")


@app.get("/help")
def help_page():
    """帮助中心页面（无需登录）。"""
    return render_template("help.html")


# ──────────────────────────────────────────────────────────────
# 认证 API
# ──────────────────────────────────────────────────────────────

@app.post("/api/register")
def api_register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400
    if len(username) < 3:
        return jsonify({"error": "用户名至少 3 个字符"}), 400
    if len(password) < 6:
        return jsonify({"error": "密码至少 6 个字符"}), 400

    user, msg = create_user(username, password)
    if not user:
        return jsonify({"error": msg}), 409

    return jsonify({"message": msg, "user": {"id": user.id, "username": user.username, "role": user.role}}), 201


@app.post("/api/login")
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400

    user, msg = authenticate_user(username, password)
    if not user:
        return jsonify({"error": msg}), 401

    login_user(user, remember=True)
    return jsonify({"message": msg, "user": {"id": user.id, "username": user.username, "role": user.role}})


@app.post("/api/logout")
@login_required
def api_logout():
    logout_user()
    return jsonify({"message": "已登出"})


@app.get("/api/me")
def api_me():
    if current_user.is_authenticated:
        return jsonify({"user": {"id": current_user.id, "username": current_user.username, "role": current_user.role}})
    return jsonify({"user": None})


@app.get("/api/me/history/stats")
@login_required
def api_me_history_stats():
    """获取当前用户的转换统计。"""
    from sqlalchemy import func

    stats = db.session.query(
        func.count(ConversionHistory.id).label("total"),
        func.coalesce(func.sum(ConversionHistory.success_count), 0).label("success"),
        func.coalesce(func.sum(ConversionHistory.failed_count), 0).label("failed"),
    ).filter_by(user_id=current_user.id).first()

    # 获取最新记录ID（用于计算注册天数）
    latest = (
        ConversionHistory.query
        .filter_by(user_id=current_user.id)
        .order_by(ConversionHistory.created_at.asc())
        .first()
    )

    return jsonify({
        "total": stats.total or 0,
        "success": int(stats.success or 0),
        "failed": int(stats.failed or 0),
        "latest_id": latest.id if latest else None,
    })


@app.get("/api/me/history")
@login_required
def api_me_history():
    """获取当前用户的转换历史（分页）。"""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)
    per_page = min(per_page, 100)

    pagination = (
        ConversionHistory.query
        .filter_by(user_id=current_user.id)
        .order_by(ConversionHistory.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return jsonify({
        "items": [h.to_dict() for h in pagination.items],
        "total": pagination.total,
        "pages": pagination.pages,
        "page": page,
    })


# ──────────────────────────────────────────────────────────────
# 转换 API
# ──────────────────────────────────────────────────────────────

@app.post("/api/convert")
def api_convert():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "输入文本为空"}), 400

    result = convert_commands_to_yaml(text)

    # 已登录用户：自动保存历史
    if current_user.is_authenticated and result.get("yaml"):
        hist = ConversionHistory(
            user_id=current_user.id,
            input_text=text[:65536],          # 截断防止过大
            output_yaml=result["yaml"][:65536],
            success_count=result.get("success", 0),
            failed_count=result.get("failed", 0),
        )
        db.session.add(hist)
        db.session.commit()

    return jsonify(result)


@app.post("/api/upload")
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    f = request.files["file"]
    if not f.filename or not f.filename.lower().endswith(".txt"):
        return jsonify({"error": "Only .txt files are supported"}), 400

    try:
        text = f.read().decode("utf-8").strip()
    except UnicodeDecodeError:
        return jsonify({"error": "File encoding must be UTF-8"}), 400

    if not text:
        return jsonify({"error": "Uploaded file is empty"}), 400

    result = convert_commands_to_yaml(text)

    if current_user.is_authenticated and result.get("yaml"):
        hist = ConversionHistory(
            user_id=current_user.id,
            input_text=text[:65536],
            output_yaml=result["yaml"][:65536],
            success_count=result.get("success", 0),
            failed_count=result.get("failed", 0),
        )
        db.session.add(hist)
        db.session.commit()

    return jsonify(result)


# ──────────────────────────────────────────────────────────────
# 转换历史 API
# ──────────────────────────────────────────────────────────────

@app.get("/api/history")
@login_required
def api_history():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    per_page = min(per_page, 100)

    pagination = (
        ConversionHistory.query
        .filter_by(user_id=current_user.id)
        .order_by(ConversionHistory.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return jsonify({
        "items": [h.to_dict() for h in pagination.items],
        "total": pagination.total,
        "pages": pagination.pages,
        "page": page,
    })


@app.delete("/api/history/<int:hist_id>")
@login_required
def api_delete_history(hist_id):
    hist = db.session.get(ConversionHistory, hist_id)
    if not hist or hist.user_id != current_user.id:
        return jsonify({"error": "记录不存在或无权删除"}), 404
    db.session.delete(hist)
    db.session.commit()
    return jsonify({"message": "已删除"})


# ──────────────────────────────────────────────────────────────
# 管理 API（需管理员）
# ──────────────────────────────────────────────────────────────

@app.get("/api/admin/users")
@login_required
@admin_required
def api_admin_users():
    users = User.query.order_by(User.id).all()
    return jsonify({
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "role": u.role,
                "created_at": u.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "conversion_count": u.conversions.count(),
            }
            for u in users
        ]
    })


@app.delete("/api/admin/users/<int:user_id>")
@login_required
@admin_required
def api_admin_delete_user(user_id):
    if user_id == current_user.id:
        return jsonify({"error": "不能删除自己"}), 400
    if delete_user_by_id(user_id):
        return jsonify({"message": "用户已删除"})
    return jsonify({"error": "用户不存在"}), 404


@app.put("/api/admin/users/<int:user_id>/password")
@login_required
@admin_required
def api_admin_change_password(user_id):
    """
    管理员重置指定用户的密码。
    若目标用户是管理员，需验证旧密码。
    若目标用户是普通用户，无需验证旧密码。
    """
    target = db.session.get(User, user_id)
    if not target:
        return jsonify({"error": "用户不存在"}), 404

    data = request.get_json(silent=True) or {}
    new_password = data.get("password", "")
    if len(new_password) < 6:
        return jsonify({"error": "密码至少 6 个字符"}), 400

    # 管理员修改自己的密码，需验证旧密码
    if target.role == "admin" and target.id == current_user.id:
        old_password = data.get("old_password", "")
        if not old_password:
            return jsonify({"error": "请输入旧密码"}), 400
        if not verify_password(old_password, target.password_hash):
            return jsonify({"error": "旧密码错误"}), 400

    target.password_hash = hash_password(new_password)
    db.session.commit()
    return jsonify({"message": f"已为用户「{target.username}」重置密码"})


@app.put("/api/profile/password")
@login_required
def api_change_own_password():
    """
    当前用户修改自己的密码（需旧密码验证）。
    """
    data = request.get_json(silent=True) or {}
    old_password = data.get("old_password", "")
    new_password = data.get("password", "")
    if len(new_password) < 6:
        return jsonify({"error": "密码至少 6 个字符"}), 400

    if not verify_password(old_password, current_user.password_hash):
        return jsonify({"error": "旧密码错误"}), 403

    current_user.password_hash = hash_password(new_password)
    db.session.commit()
    return jsonify({"message": "密码修改成功"})


@app.get("/api/admin/history")
@login_required
@admin_required
def api_admin_history():
    """查看所有用户的转换历史。"""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    per_page = min(per_page, 100)

    pagination = (
        ConversionHistory.query
        .order_by(ConversionHistory.created_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return jsonify({
        "items": [h.to_dict() for h in pagination.items],
        "total": pagination.total,
        "pages": pagination.pages,
        "page": page,
    })


@app.delete("/api/admin/history/<int:hist_id>")
@login_required
@admin_required
def api_admin_delete_history(hist_id):
    hist = db.session.get(ConversionHistory, hist_id)
    if not hist:
        return jsonify({"error": "记录不存在"}), 404
    db.session.delete(hist)
    db.session.commit()
    return jsonify({"message": "已删除"})


@app.post("/api/admin/history/batch-delete")
@login_required
@admin_required
def api_admin_batch_delete_history():
    """批量删除转换历史记录。"""
    data = request.get_json(silent=True) or {}
    ids = data.get("ids", [])
    if not ids:
        return jsonify({"error": "未提供要删除的记录ID"}), 400
    deleted = 0
    for hist_id in ids:
        hist = db.session.get(ConversionHistory, hist_id)
        if hist:
            db.session.delete(hist)
            deleted += 1
    db.session.commit()
    return jsonify({"message": f"已删除 {deleted} 条记录", "deleted": deleted})


# ──────────────────────────────────────────────────────────────
# 备份 / 恢复 API
# ──────────────────────────────────────────────────────────────

@app.get("/api/backup/export")
@login_required
@admin_required
def api_backup_export():
    """下载备份文件。支持 ?file= 指定文件，缺省下载最新的。"""
    from src.docker_converter.backup import _backup_dir
    bd = _backup_dir()
    filename = request.args.get("file", "")
    if filename:
        # 安全检查：只允许下载 backups/ 目录下的 .json 文件
        if not filename.endswith(".json") or "/" in filename or "\\" in filename:
            return jsonify({"error": "非法文件名"}), 400
        filepath = os.path.join(bd, filename)
        # 安全检查：确保路径在 backups/ 目录内，防止路径穿越
        if not os.path.normpath(filepath).startswith(os.path.normpath(bd)):
            return jsonify({"error": "非法路径"}), 400
        if not os.path.exists(filepath):
            return jsonify({"error": "备份文件不存在"}), 404
    else:
        try:
            files = sorted([f for f in os.listdir(bd) if f.endswith(".json")])
        except PermissionError:
            return jsonify({"error": "无权限访问备份目录"}), 500
        if not files:
            return jsonify({"error": "暂无备份文件"}), 404
        filepath = os.path.join(bd, files[-1])
    return send_file(
        filepath,
        as_attachment=True,
        download_name=os.path.basename(filepath),
        mimetype="application/json",
    )


@app.post("/api/backup/create")
@login_required
@admin_required
def api_backup_create():
    """创建全量备份（保存到服务端）。"""
    filepath = export_backup()
    filename = os.path.basename(filepath)
    return jsonify({"message": f"备份「{filename}」已创建", "filename": filename})


@app.get("/api/backup/list")
@login_required
@admin_required
def api_backup_list():
    return jsonify({"backups": list_backups()})


@app.post("/api/backup/import")
@login_required
@admin_required
def api_backup_import():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    f = request.files["file"]
    if not f.filename or not f.filename.lower().endswith(".json"):
        return jsonify({"error": "Only .json backup files are supported"}), 400

    # 写入临时文件
    tmp = io.BytesIO(f.read())
    tmp.seek(0)
    import json
    try:
        json.loads(tmp.getvalue().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return jsonify({"error": "Invalid JSON file"}), 400

    tmp_path = os.path.join(_DATA_DIR, "_import_tmp.json")
    with open(tmp_path, "wb") as wf:
        wf.write(tmp.getvalue())

    try:
        users_count, history_count, msg = import_backup(tmp_path)
    finally:
        os.remove(tmp_path)

    return jsonify({"message": f"{msg}，用户 {users_count} 个，历史 {history_count} 条"})


@app.delete("/api/backup/<path:filename>")
@login_required
@admin_required
def api_backup_delete(filename):
    """删除指定的备份文件。"""
    from src.docker_converter.backup import _backup_dir
    bd = _backup_dir()
    # 安全检查：只允许删除 backups/ 目录下的 .json 文件
    if not filename.endswith(".json") or "/" in filename or "\\" in filename:
        return jsonify({"error": "非法文件名"}), 400
    filepath = os.path.join(bd, filename)
    # 安全检查：确保路径在 backups/ 目录内，防止路径穿越
    if not os.path.normpath(filepath).startswith(os.path.normpath(bd)):
        return jsonify({"error": "非法路径"}), 400
    if not os.path.exists(filepath):
        return jsonify({"error": "备份文件不存在"}), 404
    try:
        os.remove(filepath)
    except PermissionError:
        return jsonify({"error": "无权限删除文件，请检查 backups 目录权限"}), 500
    except OSError as e:
        return jsonify({"error": f"删除失败：{str(e)}"}), 500
    return jsonify({"message": f"备份「{filename}」已删除"})


@app.post("/api/backup/<path:filename>/restore")
@login_required
@admin_required
def api_backup_restore(filename):
    """从指定备份文件恢复数据。"""
    from src.docker_converter.backup import _backup_dir
    bd = _backup_dir()
    if not filename.endswith(".json") or "/" in filename or "\\" in filename:
        return jsonify({"error": "非法文件名"}), 400
    filepath = os.path.join(bd, filename)
    # 安全检查：确保路径在 backups/ 目录内，防止路径穿越
    if not os.path.normpath(filepath).startswith(os.path.normpath(bd)):
        return jsonify({"error": "非法路径"}), 400
    if not os.path.exists(filepath):
        return jsonify({"error": "备份文件不存在"}), 404
    try:
        users_count, history_count, msg = import_backup(filepath)
    except Exception as e:
        return jsonify({"error": f"恢复失败：{str(e)}"}), 500
    return jsonify({"message": f"{msg}，用户 {users_count} 个，历史 {history_count} 条"})


# ──────────────────────────────────────────────────────────────
# Favicon（SVG）
# ──────────────────────────────────────────────────────────────

_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="6" fill="#2f81f7"/>'
    '<text x="16" y="23" font-size="18" text-anchor="middle" fill="white">🐳</text>'
    '</svg>'
)


@app.get("/favicon.svg")
def favicon_svg():
    return app.response_class(_FAVICON_SVG, mimetype="image/svg+xml")


@app.get("/favicon.ico")
def favicon_ico():
    """Redirect old browsers to SVG favicon."""
    return app.response_class(_FAVICON_SVG, mimetype="image/svg+xml")


# ──────────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────────

def main() -> None:
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "127.0.0.1")
    # 启动信息由启动脚本输出，避免重复
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
