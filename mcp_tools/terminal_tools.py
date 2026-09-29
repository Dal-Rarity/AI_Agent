# 控制MacOS终端的工具
import subprocess
import sys
import time
from typing import Annotated, List

from mcp.server.fastmcp import FastMCP  # 用来将工具封装到MCP中
from pydantic import Field

mcp = FastMCP(log_level="WARNING")   # MCP实例化（静默 INFO 请求日志，保持用户界面干净）

def parse_key_code(button):
    button = button.lower()

    keycode_map = {
        'return': 'return',
        'space': 'space',
        'up': 126,
        'down': 125,
        'left': 123,
        'right': 124,
        'a': 0,
        'b': 11,
        'c': 8,
        'd': 2,
        'e': 14,
        'f': 3,
        'g': 5,
        'h': 4,
        'i': 34,
        'j': 38,
        'k': 40,
        'l': 37,
        'm': 46,
        'n': 45,
        'o': 31,
        'p': 35,
        'q': 12,
        'r': 15,
        's': 1,
        't': 17,
        'u': 32,
        'v': 9,
        'w': 13,
        'x': 7,
        'y': 16,
        'z': 6,
        '.': 47,
        'dot': 47,
        '0': 29,
        '1': 18,
        '2': 19,
        '3': 20,
        '4': 21,
        '5': 23,
        '6': 22,
        '7': 26,
        '8': 28,
        '9': 25,
        '-': 27,
    }

    return keycode_map[button]

def concat_key_codes(key_codes):
    script = ''
    for key in key_codes:
        key_code = parse_key_code(key)
        script += f'keystroke {key_code}\n'
        script += 'delay 0.5\n'
    return script.strip()

def run_applescript(script):
    p = subprocess.Popen(["osascript", "-e", script],
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE,
                         )
    output, error = p.communicate()

    return output.decode("utf-8").strip(), error.decode("utf-8").strip()

# 拿到终端的ID
def get_all_terminal_window_ids() -> str:
    """获取所有终端窗口的ID列表"""
    output, error = run_applescript("""
tell application "Terminal"
    set outputList to {}
    repeat with aWindow in windows
        set windowID to id of aWindow
        set tabCount to number of tabs of aWindow
        repeat with tabIndex from 1 to tabCount
            set end of outputList to {tab tabIndex of aWindow in windowID}
        end repeat
    end repeat
end tell
return outputList""")
    if error:
        return f"错误: {error}"
    else:
        return output

@mcp.tool(name="send_terminal_keyboard_key", description="send a terminal keyboard key to an existing terminal")
def send_terminal_keyboard_key(key_codes: Annotated[List[str], Field(description="向终端输入按键", examples=["up","down"])] = "") -> bool:
    print('\nsend_terminal_keyboard_key keycode:', key_codes, file=sys.stderr, flush=True)
    print('-' * 50, file=sys.stderr, flush=True)
    script = f'''
    tell application "Terminal"
        activate
        tell application "System Events"
            {concat_key_codes(key_codes)}
        end tell
    end tell
    '''
    print(script, file=sys.stderr, flush=True)
    terminal_content, error = run_applescript(script)
    if error:
        return False
    else:
        return True

# 关闭终端
@mcp.tool(name="close_terminal", description="关闭终端")
def clos_terminal_if_open() -> str:
    """关闭终端应用程序（如果正在运行）"""
    output, error = run_applescript("""
tell application "System Events" 
    if exists process "Terminal" then
        tell process "Terminal" to quit
    end if
end tell""")
    if error:
        return f"关闭终端失败： {error}"
    else:
        return "终端已关闭"

# 打开终端并运行命令
@mcp.tool(name="open_terminal", description="打开新终端窗口")
def open_new_terminal(window_id: Annotated[str, Field(description="可选的终端窗口ID，为空则打开新终端窗口", examples=["","12345"])] = "") -> str:
    """打开新终端窗口或激活现有终端窗口"""
    if window_id:
        output, error = run_applescript(f"""
tell application "Terminal"
    if (count of windows) > 0 then
        set theWindow to window id {window_id}
        set frontmost of theWindow to true
        activate
    else
        activate
    end if
end tell""")
    else:
        output, error = run_applescript("""
tell application "Terminal"
    activate
end tell""")
    if error:
        # print(error)
        return f"打开终端失败： {error}"
    else:
        time.sleep(5)
        window_ids = get_all_terminal_window_ids()
        return f"终端已打开，窗口ID: {window_ids}"

# 在指定终端运行命令
@mcp.tool(name="run_terminal_script", description="在终端中运行命令")
def run_script_in_terminal(script: Annotated[str, Field(description="要运行的命令", examples=["pwd", "ls -al", "python --version"])]) -> str:
    """在终端中运行指定的命令"""
    output, error = run_applescript(f"""
tell application "Terminal"
    activate
    if (count of windows) > 0 then
        do script "{script}" in window 1
    else
        do script "{script}"
    end if
end tell""")
    if error:
        return f"运行命令失败： {error}"
    else:
        return f"命令已运行: {script}"

# 把命令以及工具拿给大模型的工具
@mcp.tool(name="get_terminal_text", description="获取终端的完整文本内容")
def get_terminal_full_text() -> str:
    output, error = run_applescript("""
tell application "Terminal"
    set fullTest to history of selected tab of front window
end tell""")
    if error:
        return f"获取终端文本失败： {error}"
    else:
        return f"终端文本: {output}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
    # clos_terminal_if_open()
    # open_new_terminal()
    # get_all_terminal_window_ids()
    # run_applescript("ls -al")
    get_terminal_full_text()

