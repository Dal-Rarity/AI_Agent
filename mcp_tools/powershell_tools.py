# 控制 Windows 终端的工具（PowerShell）
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, List

import psutil
import pyautogui
import pygetwindow as gw
from mcp.server.fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP(log_level="WARNING")

# ⭐ 终端状态持久化到文件（跨 MCP server 进程共享）
#    MCP server 每次工具调用都可能重启，内存里的 dict 会丢，所以必须落盘。

# __file__ = D:\Python\ai-agent-test\app\AI_Agent\mcp_tools\powershell_tools.py
# parents[0] = mcp_tools
# parents[1] = AI_Agent     ← 就是这里
_STATE_DIR = Path(__file__).resolve().parents[1] / ".temp"
_STATE_DIR.mkdir(parents=True, exist_ok=True)     # 不存在就自动创建
_STATE_FILE = _STATE_DIR / "powershell.json"


# ---------------------------------------------------------------- 状态管理

def _load_state() -> dict:
    """读取已记录的终端状态 {name: {"pid": ..., "cwd": ...}}"""
    try:
        if _STATE_FILE.exists():
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"读取终端状态失败: {e}", file=sys.stderr)
    return {}


def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"保存终端状态失败: {e}", file=sys.stderr)


def _pid_alive(pid: int) -> bool:
    try:
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


# ---------------------------------------------------------------- 底层工具

def _kill_process_tree(pid: int) -> None:
    """超时后杀掉 powershell 及其子进程（npm/node 等），避免残留占用。"""
    try:
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        parent.kill()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            stdin=subprocess.DEVNULL,
        )


def _ensure_npm_noninteractive(command: str) -> str:
    """给 npm/npx 脚手架补上 --yes，避免卡在 'Ok to proceed?'。"""
    if re.search(r"\b(npm(?:\.cmd)?|npx(?:\.cmd)?)\b", command, re.I) and not re.search(
        r"(^|[\s])(--yes|-y)([\s]|$)", command
    ):
        command = re.sub(
            r"\b(npm(?:\.cmd)?|npx(?:\.cmd)?)\b",
            r"\1 --yes",
            command,
            count=1,
            flags=re.IGNORECASE,
        )
    return command


def run_powershell_command(command: str, capture_output: bool = True, timeout: int = 90):
    """执行 PowerShell 命令，返回 (stdout, stderr, returncode)

    ⭐ 不用 shell=True：list 模式直接传参更安全，避免命令注入；
       加 -NoProfile -NonInteractive：跳过 profile 加载（快 + 避免
       执行策略报错 CLIXML 污染 stderr）。
    ⭐ stdin 必须 DEVNULL：MCP stdio 的 stdin 是 JSON-RPC 管道。子进程若继承
       stdin，npm create / npx 会把管道当成交互输入一直等 'y'，Agent 表现为卡死。
    ⭐ 执行完弹出可见窗口显示命令和结果，方便用户观察交互效果。
    """
    try:
        command = _ensure_npm_noninteractive(command)
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
        env = os.environ.copy()
        env.setdefault("CI", "true")
        env.setdefault("npm_config_yes", "true")
        popen_kwargs = {
            "stdin": subprocess.DEVNULL,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": env,
        }
        if capture_output:
            popen_kwargs["stdout"] = subprocess.PIPE
            popen_kwargs["stderr"] = subprocess.PIPE
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        proc = subprocess.Popen(cmd, **popen_kwargs)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc.pid)
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            return (
                "",
                (
                    f"命令执行超时（{timeout}秒），已终止。"
                    "交互式或长耗时命令请改用 open_powershell + "
                    "run_powershell_script + get_powershell_text。"
                    "脚手架请带非交互参数，例如："
                    "npm.cmd create vue@latest <name> --yes -- --default --force"
                ),
                1,
            )

        stdout = (stdout or "").strip()
        stderr = (stderr or "").strip()
        rc = proc.returncode

        # ⭐ 弹出可见窗口显示命令和结果（方便用户观察交互效果）
        _show_command_result(command, stdout, stderr, rc)

        return stdout, stderr, rc
    except Exception as e:
        return "", str(e), 1


def _show_command_result(command: str, stdout: str, stderr: str, rc: int) -> None:
    """弹出一个 cmd 窗口显示执行的命令和结果，窗口停留直到用户按键关闭。"""
    try:
        # 把命令和结果写到临时批处理，由 cmd /k 执行并保持窗口
        result_text = f"命令: {command}\n\n退出码: {rc}\n"
        if stdout:
            result_text += f"\n--- 输出 ---\n{stdout}\n"
        if stderr:
            result_text += f"\n--- 错误 ---\n{stderr}\n"
        # 转义双引号和特殊字符，写入临时 .txt 供 type 显示
        import tempfile
        tmp = Path(tempfile.gettempdir()) / f"ps_result_{int(time.time()*1000)}.txt"
        tmp.write_text(result_text, encoding="utf-8")
        # 用 cmd /c type 文件 && pause 弹出窗口显示，pause 保证窗口不自动关闭
        # ⭐ stdin/stdout/stderr 全部 DEVNULL：弹窗不能继承 MCP 的 stdio 管道，
        #    否则会报 0x800700e8（管道被关闭）导致窗口打不开
        subprocess.Popen(
            ["cmd", "/c", f'type "{tmp}" & echo. & pause'],
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass  # 弹窗失败不影响主流程


def get_powershell_processes():
    """返回 Agent 管理的终端（自动清理已退出的），跨进程一致"""
    state = _load_state()
    alive = {n: info for n, info in state.items() if _pid_alive(info["pid"])}
    if alive != state:
        _save_state(alive)
    return [{"name": n, "pid": i["pid"], "cwd": i.get("cwd", "")} for n, i in alive.items()]


def activate_powershell_window():
    """激活一个 PowerShell 窗口（优先用 pygetwindow 精确查找）"""
    try:
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.1

        windows = (
            gw.getWindowsWithTitle("Windows PowerShell")
            or gw.getWindowsWithTitle("PowerShell")
            or gw.getWindowsWithTitle("powershell")
        )
        if windows:
            w = windows[0]
            if w.isMinimized:
                w.restore()
            w.activate()
            time.sleep(0.5)
            return True

        # 兜底：Alt+Tab 切换
        pyautogui.hotkey("alt", "tab")
        time.sleep(0.5)
        return False
    except Exception as e:
        print(f"激活 PowerShell 窗口失败: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------- MCP 工具

@mcp.tool(name="get_powershell_processes", description="获取 Agent 管理的 PowerShell 终端")
def get_all_powershell_processes() -> str:
    """列出 Agent 自己打开的终端（不含用户手动开的）"""
    try:
        procs = get_powershell_processes()
        if not procs:
            return "当前没有 Agent 打开的终端"
        lines = ["Agent 管理的终端:"]
        for p in procs:
            lines.append(f"  {p['name']}  (PID={p['pid']}, cwd={p['cwd']})")
        return "\n".join(lines)
    except Exception as e:
        return f"获取 PowerShell 进程失败: {str(e)}"


@mcp.tool(name="close_powershell", description="关闭 Agent 打开的终端（留空关闭全部 Agent 终端，不影响用户手动开的 PowerShell）")
def close_all_powershell(
    name: Annotated[
        str,
        Field(
            description="终端名（如 'term-1'）；留空关闭 Agent 打开的全部",
            examples="term-1",
        ),
    ] = "",
) -> str:
    """只关闭 Agent 自己启动的终端，不会误杀用户手动开的 PowerShell"""
    try:
        state = _load_state()
        if not state:
            return "当前没有 Agent 打开的终端"

        targets = [name] if name else list(state.keys())
        closed = []
        for n in targets:
            info = state.pop(n, None)
            if not info:
                continue
            try:
                p = psutil.Process(info["pid"])
                p.terminate()
                closed.append(n)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        _save_state(state)

        if not closed:
            return f"没有找到需要关闭的终端: {targets}"
        return f"已关闭: {', '.join(closed)}"
    except Exception as e:
        return f"关闭 PowerShell 进程失败: {str(e)}"


@mcp.tool(name="open_powershell", description="打开新的 PowerShell 窗口")
def open_new_powershell(
    working_directory: Annotated[
        str,
        Field(
            description="可选的工作目录，为空则使用当前目录",
            examples="D:\\Users",
        ),
    ] = "",
) -> str:
    """用 WMI 创建 powershell 进程，拿到真正的 PID 并记录到状态文件

    ⭐ 为什么用 WMI 而不是 Popen+CREATE_NEW_CONSOLE：
       MCP stdio 模式下，client 用 Job Object(KILL_ON_JOB_CLOSE) 管理 server
       进程树，工具调用结束会杀掉整树。若用 Popen 直接启动 powershell，它作为
       server 的子进程会被自动收纳进 Job、连杀掉，窗口无法存活到下次工具调用。
       用 WMI Win32_Process.Create 启动，powershell 的父进程是 WMI 服务
       (WmiPrvSE.exe)，不在 server 进程树内，不被 Job 收纳，窗口保持存活。
    """
    try:
        # ⭐ 解析成绝对路径，避免出现 cwd=.
        if working_directory and os.path.isdir(working_directory):
            cwd = os.path.abspath(working_directory)
        else:
            cwd = os.getcwd()

        # 先分配终端名 + 该终端专属的 transcript 日志文件
        state = _load_state()
        used_idx = set()
        for n in state:
            if n.startswith("term-"):
                try:
                    used_idx.add(int(n.split("-", 1)[1]))
                except ValueError:
                    pass
        idx = 1
        while idx in used_idx:
            idx += 1
        name = f"term-{idx}"
        log_file = _STATE_DIR / f"{name}.log"
        log_file.unlink(missing_ok=True)  # 删旧日志，避免内容混淆

        # ⭐ WMI 创建进程：父进程是 WMI 服务(WmiPrvSE.exe)，不在 server 进程树内，
        #    不被 client 的 Job Object(KILL_ON_JOB_CLOSE) 收纳，窗口保持存活。
        #    ShowWindow=1(SW_SHOWNORMAL) 确保窗口可见。
        #    挂 Start-Transcript：终端里的所有输入/输出(含交互式提问)都会写入
        #    log 文件，get_powershell_text 靠它读取屏幕内容。
        #    路径里的单引号翻倍转义，防止破坏 PS 脚本。
        log_safe = str(log_file).replace("'", "''")
        cwd_safe = cwd.replace("'", "''")
        wmi_script = (
            "$startup = ([wmiclass]'Win32_ProcessStartup').CreateInstance(); "
            "$startup.ShowWindow = 1; "
            # 注意：PS 字符串里转义双引号用反引号 `"（不是 \"）
            f"$cmd = \"powershell -NoExit -Command `\"Start-Transcript -LiteralPath '{log_safe}' | Out-Null`\"\"; "
            f"$r = ([wmiclass]'Win32_Process').Create($cmd, '{cwd_safe}', $startup); "
            "Write-Output $r.ProcessId"
        )
        stdout, stderr, rc = run_powershell_command(wmi_script)
        if rc != 0 or not stdout.strip():
            return f"打开 PowerShell 失败(WMI): {stderr or '未拿到 PID'}"

        pid = int(stdout.strip())
        time.sleep(1.5)  # 等 transcript 启动完成

        state[name] = {"pid": pid, "cwd": cwd, "log": str(log_file)}
        _save_state(state)

        return (
            f"已打开终端 '{name}' (PID={pid}, cwd={cwd})。"
            "后续执行命令、读输出、发按键或关闭时用这个名字。"
        )
    except Exception as e:
        return f"打开 PowerShell 失败: {str(e)}"


@mcp.tool(name="run_powershell_script", description="通过 pyautogui 向 PowerShell 窗口发送命令")
def run_powershell_script(
    script: Annotated[
        str,
        Field(
            description="要在 PowerShell 窗口中执行的脚本命令",
            examples="Get-Location",
        ),
    ],
) -> str:
    """向已激活的 PowerShell 窗口发送命令（GUI 自动化，命令显示在真实窗口中）"""
    try:
        procs = get_powershell_processes()
        if not procs:
            return "没有 Agent 打开的终端，请先调用 open_powershell"

        if not activate_powershell_window():
            return "无法激活 PowerShell 窗口，请确保窗口未最小化、未被其它窗口遮挡"

        # ⭐ 用剪贴板粘贴代替 pyautogui.write：write 是键盘按键模拟，会被中文
        #    输入法(IME)拦截——空格被当候选词确认、标点转中文(,.→，。")、字母
        #    被当拼音转汉字(rite→日特)，命令被破坏后 PowerShell 无法识别。
        #    剪贴板粘贴直接插入文本，不经过键盘事件，不受 IME 影响。
        #    base64 编码避免命令里的引号/特殊字符在拼脚本时被转义破坏。
        import base64
        b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        set_clip = (
            f'$b=[Convert]::FromBase64String("{b64}"); '
            f'$s=[Text.Encoding]::Unicode.GetString($b); '
            f'Set-Clipboard -Value $s'
        )
        run_powershell_command(set_clip)
        time.sleep(0.1)

        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.2)
        pyautogui.press("end")
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3)
        pyautogui.press("enter")
        return f"命令已发送到终端: {script}"
    except Exception as e:
        return f"发送 PowerShell 命令失败: {str(e)}"


@mcp.tool(name="send_powershell_key", description="向 PowerShell 终端发送键盘按键（用于回答 npm create vue 等交互式命令的提问）")
def send_powershell_key(
    keys: Annotated[
        List[str],
        Field(
            description="要发送的按键列表，按顺序逐个按下。常用: enter(确认/用默认值)、up/down(移动选项)、"
                       "space(勾选)、tab、esc、y、n、a-z、0-9",
            examples=[["enter"], ["down", "down", "enter"], ["y", "enter"]],
        ),
    ],
    name: Annotated[
        str,
        Field(description="终端名（如 'term-1'）；留空发给第一个 Agent 终端", examples="term-1"),
    ] = "",
) -> str:
    """发送键盘按键到已激活的 PowerShell 窗口（交互式脚手架提问时使用）"""
    try:
        procs = get_powershell_processes()
        if not procs:
            return "没有 Agent 打开的终端，请先调用 open_powershell"

        if not activate_powershell_window():
            return "无法激活 PowerShell 窗口，请确保窗口未最小化、未被其它窗口遮挡"

        # 按键名归一化（对齐 Mac 版 parse_key_code 的命名习惯）
        key_map = {"return": "enter", "del": "delete", "esc": "escape"}
        sent = []
        for k in keys:
            key = key_map.get(str(k).lower(), str(k).lower())
            pyautogui.press(key)
            time.sleep(0.15)
            sent.append(key)
        return f"已向终端发送按键: {', '.join(sent)}"
    except Exception as e:
        return f"发送按键失败: {str(e)}"


@mcp.tool(name="get_powershell_text", description="获取 PowerShell 终端最近的输出文本（看交互式命令的提问、执行进度与结果）")
def get_powershell_text(
    name: Annotated[
        str,
        Field(description="终端名（如 'term-1'）；留空读第一个 Agent 终端", examples="term-1"),
    ] = "",
    tail: Annotated[
        int,
        Field(description="读取最后多少行（1~500，默认100）", examples=100),
    ] = 100,
) -> str:
    """读取终端 transcript 日志的最后 N 行，等价于看屏幕上最近的内容"""
    try:
        state = _load_state()
        if not state:
            return "当前没有 Agent 打开的终端"

        target = name if name in state else next(iter(state))
        info = state[target]
        log_file = info.get("log", "")
        if not log_file or not Path(log_file).exists():
            return f"终端 '{target}' 还没有输出记录"

        tail = max(1, min(int(tail), 500))
        log_safe = log_file.replace("'", "''")
        # ⭐ 用 PowerShell Get-Content 读：transcript 文件正被终端占用，
        #    Python 直接 open 可能撞 sharing violation，Get-Content 不受影响。
        stdout, stderr, rc = run_powershell_command(
            f"Get-Content -LiteralPath '{log_safe}' -Tail {tail}",
            timeout=20,
        )
        if rc != 0:
            return f"读取终端文本失败: {stderr}"
        return f"终端 '{target}' 最近 {tail} 行:\n{stdout}" if stdout else "终端暂无输出"
    except Exception as e:
        return f"获取终端文本失败: {str(e)}"


@mcp.tool(
    name="execute_powershell_command",
    description=(
        "后台直接执行短命令并立刻返回结果（不经过窗口）。"
        "只用于立刻出结果的命令：node -v、npm.cmd -v、Test-Path、New-Item、Get-ChildItem。"
        "禁止用于会提问的交互命令（裸 npm create / vue create 会卡住）。"
        "创建 Vue 请用非交互："
        "cmd /c \"cd /d <绝对路径> && npm.cmd create vue@latest <name> --yes -- --default --force\"。"
        "npm install 等长耗时命令请用 open_powershell + run_powershell_script。"
    ),
)
def execute_powershell_command(
    command: Annotated[
        str,
        Field(
            description="要执行的 PowerShell 命令。npm/npx 会自动补 --yes。交互式脚手架必须带 --default/--force。",
            examples="Get-Process",
        ),
    ],
) -> str:
    """后台直接执行，不依赖窗口（推荐用于所有"拿结果"的场景）"""
    try:
        stdout, stderr, returncode = run_powershell_command(command, timeout=180)
        if returncode != 0:
            return f"命令执行失败: {stderr}" if stderr else "命令执行失败，但没有错误信息"
        return f"命令执行成功:\n{stdout}" if stdout else "命令执行成功，但没有输出"
    except Exception as e:
        return f"执行 PowerShell 命令失败: {str(e)}"


# ---------------------------------------------------------------- 入口

if __name__ == "__main__":
    # MCP stdio 模式：stdout 只能承载 JSON-RPC，任何 print 都会破坏协议。
    # 所有调试信息请写 stderr（print(..., file=sys.stderr)）。
    mcp.run(transport="stdio")
