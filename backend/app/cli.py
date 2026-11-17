"""只在可信主机终端执行的离线初始化命令。"""

from __future__ import annotations

import argparse
import getpass
import sys

from app.core.database import initialize_database
from app.core.errors import AppError
from app.services.auth_service import AuthService


def initialize_admin(identifier: str) -> int:
    """交互式创建唯一首位系统管理员，不设置或输出默认密码。"""
    password = getpass.getpass("设置系统管理员密码（至少 12 位）：")
    confirmation = getpass.getpass("再次输入密码：")
    if password != confirmation:
        print("两次输入的密码不一致。", file=sys.stderr)
        return 2
    initialize_database()
    try:
        user = AuthService().create_user(
            identifier, password, "SYSTEM_ADMIN", initial_admin=True
        )
    except (AppError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"系统管理员已创建：{user.login_identifier}（{user.id}）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="劳动权益咨询问答台离线管理命令")
    commands = parser.add_subparsers(dest="command", required=True)
    admin_parser = commands.add_parser(
        "init-admin", help="交互式创建唯一首位系统管理员"
    )
    admin_parser.add_argument("--login-identifier", required=True)
    arguments = parser.parse_args()
    if arguments.command == "init-admin":
        return initialize_admin(arguments.login_identifier)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
