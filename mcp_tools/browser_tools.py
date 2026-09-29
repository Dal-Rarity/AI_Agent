import re
import sys
import time
from typing import Annotated

# 注意：本文件是 MCP stdio 服务，stdout 只能传输 JSON-RPC 消息。
# 所有调试/日志输出必须写到 stderr（print(..., file=sys.stderr)），
# 否则普通 print 的内容会插入协议流，导致客户端报 "Failed to parse JSONRPC message"。

from bs4 import BeautifulSoup, Comment
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from webdriver_manager.microsoft import EdgeChromiumDriverManager

mcp = FastMCP(log_level="WARNING")

service = Service(EdgeChromiumDriverManager().install())

# 百度存在反爬系统，用于模拟真实用户访问
options = Options()

# 1. 关闭自动化控制提示（关键）
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option("useAutomationExtension", False)

# 2. 禁用 Blink 引擎的自动化控制特性（关键）
options.add_argument("--disable-blink-features=AutomationControlled")

# 3. 设置一个真实的 User-Agent
options.add_argument(
    "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0")

def _clone_options(src: Options) -> Options:
    """克隆一份 Options（拷贝 arguments 和 experimental_options），不动原对象"""
    dst = Options()
    for arg in src.arguments:
        dst.add_argument(arg)
    for k, v in src.experimental_options.items():
        dst.add_experimental_option(k, v)
    return dst

# 绑定端口号用于需登录网页访问,需手动打开Edge浏览器，告诉浏览器就是真人启动
def get_Edge_instance(need_login: bool = False, debugger_address: str = "127.0.0.1:9090"):
    """
        :param need_login:
            False → 模拟真实用户访问，Selenium 自动启动浏览器（用全局 options）
            True  → 绑定端口号用于需登录网页访问，连接手动打开的 Edge
                    （克隆全局 options 后加 debuggerAddress，不动全局）
        """
    if need_login:
        # 【路线二】需登录网页访问：基于全局配置派生一份，追加 debuggerAddress
        opts = _clone_options(options)
        opts.add_experimental_option("debuggerAddress", debugger_address)
    else:
        # 【路线一】模拟真实用户访问：直接用全局 options
        opts = options

    driver = webdriver.Edge(service=service, options=opts)

    # CDP 注入：连接已有浏览器时唯一真正有效的反检测（两种模式都加上）
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })

    if need_login:
        print(f"成功连接到Edge浏览器（真人启动），当前URL：{driver.current_url}", file=sys.stderr, flush=True)
    else:
        print(f"成功启动Edge浏览器（模拟真实用户），当前URL：{driver.current_url}", file=sys.stderr, flush=True)

    return driver

def open_Edge():
    driver = get_Edge_instance()
    driver.get("https://www.baidu.com")
    print(driver.current_url, file=sys.stderr, flush=True)

    time.sleep(5)
    # 通过js打开新标签
    driver.execute_script("window.open('https://www.qq.com', '_blank');")           # _blank 表示新标签页

    # 获取所有句柄
    all_handles = driver.window_handles
    print(all_handles, file=sys.stderr, flush=True)

    time.sleep(5)
    # 切换句柄
    driver.switch_to.window(all_handles[-1])
    print(driver.current_url, file=sys.stderr, flush=True)

    driver.switch_to.window(all_handles[0])
    print(driver.current_url, file=sys.stderr, flush=True)


# 基于selenium的浏览器在百度自动搜索
# @mcp_tools.tool(description="基于selenium的浏览器在百度自动搜索")
def search_in_baidu(query: str) -> str:
    driver = get_Edge_instance()
    try:
        driver.get("https://www.baidu.com")

        # 查找并等待页面元素出现
        text_box = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.ID, "chat-textarea"))
        )

        text_box.send_keys(query)

        # 查找并等待提交按钮出现
        submit_button = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "chat-submit-button"))
        )

        submit_button.click()
        # 获取搜索页面结果
        # 1.先获取标题信息
        WebDriverWait(driver, 10).until(
            EC.title_contains(query[:10])
        )
        # 翻页结果优化
        page_text_list = []
        for i in range(3):
            if i > 0:
                results = WebDriverWait(driver, 10).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".page-inner_2jZi2>a"))
                )
                # 取最后一个 a 作为"下一页"，并判断是否 disabled
                next_btn = results[-1]              # 永远取最后一个"下一页"
                if "disabled" in (next_btn.get_attribute("class") or ""):
                    break  # 已是最后一页，结束翻页
                next_btn.click()
                time.sleep(2)

            # 2. 获取查询结果
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )

            # 滚动优化
            last_height = driver.execute_script("return document.body.scrollHeight")
            while True:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                # 检查是否加载完成
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height

            page_content = driver.find_element(By.TAG_NAME, "body")
            # 将搜索结果进行翻页
            page_text = page_content.text
            page_text_list.append(f"第{i+1}页" + page_text + "\n --- \n")

        return '\n'.join(page_text_list)

    except Exception as e:
        print(f"错误: {e}", file=sys.stderr, flush=True)
        return ""
    finally:
        driver.quit()

# 搜索结果获取HTML
@mcp.tool(description="基于selenium的浏览器在百度自动搜索")
def search_in_baidu_with_html(query: str) -> str:
    driver = get_Edge_instance()
    try:
        driver.get("https://www.baidu.com")

        # 查找并等待页面元素出现
        text_box = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.ID, "chat-textarea"))
        )

        text_box.send_keys(query)

        # 查找并等待提交按钮出现
        submit_button = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "chat-submit-button"))
        )

        submit_button.click()
        # 获取搜索页面结果
        # 1.先获取标题信息
        WebDriverWait(driver, 10).until(
            EC.title_contains(query[:10])
        )
        # 翻页结果优化
        page_text_list = []
        for i in range(1):
            if i > 0:
                results = WebDriverWait(driver, 10).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".page-inner_2jZi2>a"))
                )
                # 取最后一个 a 作为"下一页"，并判断是否 disabled
                next_btn = results[-1]              # 永远取最后一个"下一页"
                if "disabled" in (next_btn.get_attribute("class") or ""):
                    break  # 已是最后一页，结束翻页
                next_btn.click()
                time.sleep(2)

            # 2. 获取查询结果
            WebDriverWait(driver, 10).until(
                # EC.presence_of_element_located((By.TAG_NAME, "body"))
                EC.presence_of_element_located((By.ID, "container"))
            )

            # 滚动优化
            last_height = driver.execute_script("return document.body.scrollHeight")
            while True:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                # 检查是否加载完成
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height

            # page_content = driver.find_element(By.TAG_NAME, "body")
            page_content = driver.find_element(By.ID, "container")

            # 将搜索结果进行翻页
            page_text = page_content.get_attribute("innerHTML")
            page_text_list.append(page_text)

        html =  '\n'.join(page_text_list)
        return pretty_html(html)

    except Exception as e:
        print(f"错误: {e}", file=sys.stderr, flush=True)
        return ""
    finally:
        driver.quit()


# 获取到的HTML结果优化
def pretty_html(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup.find_all(['script', 'style', 'link', 'meta', 'symbol', 'path', 'canvas', 'svg']):
        tag.extract()                   # 移除标签

    # 2.移除所有display: none的标签
    display_none_re = re.compile(r"display\s*:\s*none", re.IGNORECASE)
    for tag in soup.find_all(True):
        style = tag.get("style", "")
        if display_none_re.search(style):
            tag.extract()                   # 移除标签

    # 3.移除所有代码注释
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()                   # 移除标签

    # 4.移除大模型可能不需要的属性，比如class
    for tag in soup.find_all(True):
        if tag.name == "a":
            if "href" in tag.attrs:
                if "javascript" in tag.attrs["href"] or "/" == tag.attrs["href"]:
                    tag.extract()
                else:
                    tag.attrs = {"href": tag.attrs["href"]}
        else:
            tag.attrs = {}

    html = soup.prettify()

    return html


# ==================== 通用浏览器工具（任意网址，不仅限于百度搜索） ====================

def _normalize_url(url: str) -> str:
    """补全协议头并做基本校验。"""
    url = url.strip()
    if not url:
        raise ValueError("网址不能为空")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


@mcp.tool(name="open_webpage",
          description="用 Edge 浏览器打开任意网址，返回网页标题与页面正文（真实渲染，支持 JS 动态页面）")
def open_webpage(
    url: Annotated[str, Field(
        description="要打开的网址",
        json_schema_extra={"example": "https://www.baidu.com"},
    )],
) -> str:
    url = _normalize_url(url)
    driver = get_Edge_instance()
    try:
        driver.set_page_load_timeout(30)
        driver.get(url)

        # 等待 body 出现（JS 渲染页面）
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(2)  # 给动态内容一点渲染时间

        title = driver.title or "(无标题)"
        body_text = driver.find_element(By.TAG_NAME, "body").text
        # 正文折叠空白并截断，避免单条结果过长
        body_text = " ".join(body_text.split())
        if len(body_text) > 3000:
            body_text = body_text[:3000] + "...(正文过长已截断)"
        return f"网址：{driver.current_url}\n标题：{title}\n正文：\n{body_text}"
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr, flush=True)
        return f"错误: 打开网页失败：{e}"
    finally:
        driver.quit()


@mcp.tool(name="get_page_title",
          description="用 Edge 浏览器打开网址，只返回网页标题（比 open_webpage 更快、更省 token）")
def get_page_title(
    url: Annotated[str, Field(
        description="要获取标题的网址",
        json_schema_extra={"example": "https://www.baidu.com"},
    )],
) -> str:
    url = _normalize_url(url)
    driver = get_Edge_instance()
    try:
        driver.set_page_load_timeout(30)
        driver.get(url)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        return f"{driver.current_url} → 标题：{driver.title or '(无标题)'}"
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr, flush=True)
        return f"错误: 获取标题失败：{e}"
    finally:
        driver.quit()



if __name__ == "__main__":
    mcp.run(transport="stdio")
    # res = search_in_baidu_with_html("昆明的天气")
    # print(res)
    # get_Edge_instance()
    # open_Edge()
