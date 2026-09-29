import os.path
import shlex
import subprocess
import sys
import tempfile
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP

def run_limavm_shell_command(command):
    try:
        wrapper_command = "limactl shell lima-test" + command
        shell_command = shlex.split(wrapper_command)
        print("shell command:", shell_command, file=sys.stderr, flush=True)


        res = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=180,
        )
        if res.returncode != 0:
            return res.stderr
        return res.stdout
    except subprocess.TimeoutExpired:
        return "命令执行超时（180秒）。交互式命令会卡住，请改用非交互参数（如 npm.cmd create ... --yes）。"
    except Exception as e:
        return f"错误: {e}"

def run_limavm_command(command):
    try:
        wrapper_command = "limactl" + command
        shell_command = shlex.split(wrapper_command)
        print("shell command:", shell_command, file=sys.stderr, flush=True)

        res = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=180,
        )
        if res.returncode != 0:
            return res.stderr
        return res.stdout
    except subprocess.TimeoutExpired:
        return "命令执行超时（180秒）。交互式命令会卡住，请改用非交互参数（如 npm.cmd create ... --yes）。"
    except Exception as e:
        return f"错误: {e}"

# 在虚拟机中创建目录
@mcp.tool(name="make_dir_in_vm", description="在指定的虚拟机中创建目录，相当于 mkdir -p 命令")
def make_dir_in_vm(dir_path: Annotated[str, Field(description="要创建的目录路径", json_schema_extra={"example": r'D:\Python\ai-agent-test\app\code_agent'})]):
    """在虚拟机中创建目录"""
    print("在虚拟机中创建目录:", dir_path, file=sys.stderr, flush=True)
    res = run_limavm_shell_command("mkdir -p" + dir_path)
    return res

# 在虚拟机中查看指定下的目录
@mcp.tool(name="ls_dir_in_vm", description="在指定的虚拟机中查看指定下的目录，相当于 ls -la 命令")
def ls_dir_in_vm(dir_path: Annotated[str, Field(description="要创建的目录路径", json_schema_extra={"example": r'D:\Python\ai-agent-test\app\code_agent'})]):
    """在虚拟机中查看指定下的目录"""
    print("在虚拟机中查看目录:", dir_path, file=sys.stderr, flush=True)
    res = run_limavm_shell_command("ls -la" + dir_path)
    return res

# 将文件写入到虚拟机中
@mcp.tool(name="write_file_in_vm", description="将指定文件写入到虚拟机中")
def write_file_to_vm(file_path: Annotated[str, Field(description="写入的虚拟机中的文件路径", json_schema_extra={"example": r'D:\Python\ai-agent-test\app\code_agent\test.txt'})],
                     content: Annotated[str, Field(description="要写入的文件内容", json_schema_extra={"example": "这是要写入的文件内容"})]):
    """将文件写入到虚拟机中"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8") as temp_file:
        temp_file.write(content)
        temp_file_path = temp_file.name
    print("本地临时文件已创建:", temp_file_path, file=sys.stderr, flush=True)
    run_limavm_command(f"""copy {temp_file_path} lima_test:{file_path}""")
    change_file_premission_in_vm(file_path, "755")

def change_file_premission_in_vm(file_path, mode):
    """改变文件权限"""
    return run_limavm_shell_command(f"chmod {mode} {file_path}")


# 将本地文件夹上传到虚拟机
@mcp.tool(name="upload_directory_to_vm", description="将本地文件目录上传至虚拟机指定目录")
def upload_directory_to_vm(
        local_dir: Annotated[str, Field(description="本地文件夹路径", json_schema_extra={"example": r'D:\Python\ai-agent-test\app\code_agent'})],
        vm_dest_dir: Annotated[str, Field(description="虚拟机目标目录路径", json_schema_extra={"example": '/home/lima-test'})],
):
    if not os.path.exists(local_dir):
        msg = f"本地目录不存在: {local_dir}"
        print(f"错误: {msg}", file=sys.stderr, flush=True)
        return msg

    if not os.path.isdir(local_dir):
        msg = f"指定路径不是文件夹: {local_dir}"
        print(f"错误: {msg}", file=sys.stderr, flush=True)
        return msg

    make_dir_in_vm(vm_dest_dir)

    # 上传文件
    for root, dirs, files in os.walk(local_dir):
        if "node_modules" in dirs:
            dirs.remove("node_modules")
        if ".git" in dirs:
            dirs.remove(".git")
        print(root, dirs, files, file=sys.stderr, flush=True)

    # 拼接相对路径
    rel_path = os.path.relpath(root, local_dir)
    print("相对路径:", rel_path, file=sys.stderr, flush=True)

    # 创建远程文件夹  拷贝
    vm_subdir = os.path.join(vm_dest_dir, rel_path)
    print("远程文件夹:", vm_subdir, file=sys.stderr, flush=True)
    make_dir_in_vm(vm_subdir)

    # 远程上传
    for file_name in files:
        local_file_path = os.path.join(root, file_name)
        vm_file_path = os.path.join(vm_subdir, file_name)
        result = run_limavm_command(f"""copy {local_file_path} lima_test:{vm_file_path}""")
        print(result, file=sys.stderr, flush=True)

    return f"上传 [{local_dir}] 目录至lima_test: [{vm_dest_dir}] 目录成功"



if __name__ == "__main__":
    mcp.run(transport="stdio")