# 原生控制终端工具
import shlex  # shlex处理命令行参数的模块
import subprocess  # subprocess发送终端命令行的模块
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP(log_level="WARNING")

@mcp.tool(name="run_shell", description="运行 shell 命令")
def run_shell_command(command: Annotated[str, Field(description="要运行的 shell 命令")]) -> str:
    try:
        # args = shlex.split(command, comments=True)      # 处理命令行参数(识别注释）
        args = shlex.split(command)
        # print(args)

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

def run_shell_command_by_popen(commands):
    p = subprocess.Popen(commands, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=True)
    stdout, stderr = p.communicate()
    if stdout:
        return stdout
    return stderr


# print(run_shell_command("ls -al"))
# success, failed = run_shell_command_by_popen("ls -al")
# print(success)


mcp.run(transport="stdio")
