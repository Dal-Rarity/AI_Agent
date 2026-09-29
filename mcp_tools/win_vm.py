"""
AI Agent 沙盒 MCP 工具（Windows + VirtualBox + SSH 版）
用 ssh / scp 操作 Ubuntu 虚拟机（192.168.56.200）
所有文件操作被限制在 SANDBOX_ROOT 内，防止 AI 逃逸
"""
import os
import posixpath
import shlex
import subprocess
import sys
import tempfile
import threading
from typing import Annotated

import paramiko

# ⭐ 本文件是 MCP stdio 服务，stdout 只能传输 JSON-RPC 消息。
#    所有调试/日志输出必须写到 stderr（print(..., file=sys.stderr)），
#    否则普通 print 会插入协议流，导致客户端报 "Failed to parse JSONRPC message"。

from mcp.server.fastmcp import FastMCP
from pydantic import Field

# ==================== 沙盒连接配置 ====================
VM_HOST = "192.168.56.200"
VM_USER = "qi"
VM_SSH_KEY = os.path.expanduser(r"~\.ssh\id_rsa")
SANDBOX_ROOT = "/home/qi/sandbox"
CMD_TIMEOUT = 180
# ====================================================

mcp = FastMCP("vm_sandbox_tools", log_level="WARNING")


# ==================== 持久 SSH 连接 ====================
# Windows OpenSSH 9.5p1 不支持 ControlMaster 多路复用（实测报 getsockname failed: Not a socket），
# 无法通过 ssh CLI 复用连接；改用 paramiko 在 MCP server 进程内维护常驻连接。
# MCP server 进程随 agent 常驻，连接可跨工具调用复用，每条命令省去 5~8s 的 TCP+认证握手开销。
_ssh_client = None
_ssh_lock = threading.Lock()  # FastMCP 可能在线程池中执行工具调用，保护共享连接


def _get_ssh_client():
    """获取（必要时重建）模块级持久 SSH 连接；连接断开时自动重连。"""
    global _ssh_client
    if _ssh_client is not None:
        transport = _ssh_client.get_transport()
        if transport is not None and transport.is_active():
            return _ssh_client
        # 连接已死：关闭旧连接后重建
        try:
            _ssh_client.close()
        except Exception:
            pass
        _ssh_client = None

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=VM_HOST,
        username=VM_USER,
        key_filename=VM_SSH_KEY,
        timeout=10,
        banner_timeout=10,
        auth_timeout=10,
    )
    _ssh_client = client
    return client


def run_vm_shell_command(command: str) -> str:
    """通过持久 SSH 连接在虚拟机里执行 shell 命令"""
    with _ssh_lock:
        try:
            client = _get_ssh_client()
            _, stdout, stderr = client.exec_command(command, timeout=CMD_TIMEOUT)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            if stdout.channel.recv_exit_status() != 0:
                return err or out
            return out
        except Exception as e:
            # 连接类异常：重置缓存连接，下次调用时自动重建
            global _ssh_client
            _ssh_client = None
            return f"错误: {e}"


def run_local_command(cmd_list: list) -> str:
    """在本机（Windows）执行命令，主要用于 scp"""
    try:
        res = subprocess.run(
            cmd_list,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=CMD_TIMEOUT,
        )
        if res.returncode != 0:
            return res.stderr or res.stdout
        return res.stdout
    except subprocess.TimeoutExpired:
        return f"命令执行超时（{CMD_TIMEOUT}秒）。"
    except Exception as e:
        return f"错误: {e}"


def _ensure_in_sandbox(path: str) -> str:
    """
    路径安全校验：必须在 SANDBOX_ROOT 内。
    注意：这里处理的是 Linux 路径，必须用 posixpath，不能用 os.path（Windows 会转成反斜杠）
    支持两种输入：
      - 绝对路径：/home/qi/sandbox/xxx
      - 相对路径：xxx  → 自动拼成 /home/qi/sandbox/xxx
    """
    # 相对路径 → 拼到沙盒根
    if not path.startswith("/"):
        path = posixpath.join(SANDBOX_ROOT, path)

    # 用 posixpath 归一化（处理 ..、.、重复斜杠）
    norm = posixpath.normpath(path)

    if not (norm == SANDBOX_ROOT or norm.startswith(SANDBOX_ROOT + "/")):
        raise ValueError(f"🚫 路径越界，必须在 {SANDBOX_ROOT} 内，收到: {path}")
    return norm


@mcp.tool(name="health_check", description="健康检查：确认 SSH 通道与沙盒是否可用")
def health_check() -> str:
    print("执行健康检查...", file=sys.stderr, flush=True)
    return run_vm_shell_command("echo SANDBOX-OK && uname -a && whoami")


@mcp.tool(name="make_dir_in_vm", description="在虚拟机中创建目录，相当于 mkdir -p 命令")
def make_dir_in_vm(
    dir_path: Annotated[str, Field(
        description="要创建的目录路径（必须在沙盒内）",
        json_schema_extra={"example": "/home/qi/sandbox/test"},
    )],
):
    dir_path = _ensure_in_sandbox(dir_path)
    print("在虚拟机中创建目录:", dir_path, file=sys.stderr, flush=True)
    return run_vm_shell_command(f"mkdir -p {shlex.quote(dir_path)}")


@mcp.tool(name="ls_dir_in_vm", description="查看虚拟机指定目录，相当于 ls -la 命令")
def ls_dir_in_vm(
    dir_path: Annotated[str, Field(
        description="要查看的目录路径（必须在沙盒内）",
        json_schema_extra={"example": "/home/qi/sandbox"},
    )],
):
    dir_path = _ensure_in_sandbox(dir_path)
    print("在虚拟机中查看目录:", dir_path, file=sys.stderr, flush=True)
    return run_vm_shell_command(f"ls -la {shlex.quote(dir_path)}")


@mcp.tool(name="read_file_in_vm", description="读取虚拟机中指定文件的内容")
def read_file_in_vm(
    file_path: Annotated[str, Field(
        description="要读取的文件路径（必须在沙盒内）",
        json_schema_extra={"example": "/home/qi/sandbox/test.txt"},
    )],
):
    file_path = _ensure_in_sandbox(file_path)
    print("读取文件:", file_path, file=sys.stderr, flush=True)
    return run_vm_shell_command(f"cat {shlex.quote(file_path)}")


@mcp.tool(name="write_file_to_vm", description="将指定内容写入虚拟机中的文件（自动创建父目录）")
def write_file_to_vm(
    file_path: Annotated[str, Field(
        description="写入虚拟机中的文件路径（必须在沙盒内）",
        json_schema_extra={"example": "/home/qi/sandbox/test.txt"},
    )],
    content: Annotated[str, Field(
        description="要写入的文件内容",
        json_schema_extra={"examples": ["hello sandbox"]},
    )],
):
    file_path = _ensure_in_sandbox(file_path)

    with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8", suffix=".tmp") as temp_file:
        temp_file.write(content)
        temp_file_path = temp_file.name
    print("本地临时文件已创建:", temp_file_path, file=sys.stderr, flush=True)

    try:
        parent_dir = os.path.dirname(file_path)
        run_vm_shell_command(f"mkdir -p {shlex.quote(parent_dir)}")

        scp_args = [
            "scp",
            "-i", VM_SSH_KEY,
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            temp_file_path,
            f"{VM_USER}@{VM_HOST}:{file_path}",
        ]
        print("scp command:", scp_args, file=sys.stderr, flush=True)
        res = run_local_command(scp_args)
        return f"✅ 已写入: {file_path}\n{res}"
    finally:
        try:
            os.unlink(temp_file_path)
        except Exception:
            pass


@mcp.tool(name="change_file_permission_in_vm", description="修改虚拟机中文件的权限，如 chmod 755")
def change_file_permission_in_vm(
    file_path: Annotated[str, Field(
        description="文件路径（必须在沙盒内）",
        json_schema_extra={"example": "/home/qi/sandbox/test.txt"},
    )],
    mode: Annotated[str, Field(
        description="权限模式，如 755",
        json_schema_extra={"example": "755"},
    )],
):
    file_path = _ensure_in_sandbox(file_path)
    return run_vm_shell_command(f"chmod {mode} {shlex.quote(file_path)}")


@mcp.tool(name="upload_directory_to_vm", description="将 Windows 本地目录上传到虚拟机指定目录")
def upload_directory_to_vm(
    local_dir: Annotated[str, Field(
        description="本地文件夹路径",
        json_schema_extra={"example": r"D:\Python\ai-agent-test\app\code_agent"},
    )],
    vm_dest_dir: Annotated[str, Field(
        description="虚拟机目标目录（必须在沙盒内）",
        json_schema_extra={"example": "/home/qi/sandbox/uploads"},
    )],
):
    if not os.path.exists(local_dir):
        return f"❌ 本地目录不存在: {local_dir}"
    if not os.path.isdir(local_dir):
        return f"❌ 指定路径不是文件夹: {local_dir}"

    vm_dest_dir = _ensure_in_sandbox(vm_dest_dir)
    run_vm_shell_command(f"mkdir -p {shlex.quote(vm_dest_dir)}")

    scp_args = [
        "scp", "-r",
        "-i", VM_SSH_KEY,
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        local_dir,
        f"{VM_USER}@{VM_HOST}:{vm_dest_dir}/",
    ]
    print("scp -r command:", scp_args, file=sys.stderr, flush=True)
    res = run_local_command(scp_args)
    return f"✅ 已上传 [{local_dir}] → [{vm_dest_dir}/]\n{res}"


if __name__ == "__main__":
    mcp.run(transport="stdio")