# Pixiv 下载器（修复AJAX解析版）

这是一个用于从Pixiv下载图片的Python脚本，修复了AJAX解析的问题。该脚本能够提取图片URL，支持批量下载，并且具备重试机制和日志记录功能。

## 功能

- **修复AJAX解析**：正确处理AJAX请求并提取图片URL。
- **批量下载**：支持从Pixiv批量下载图片。
- **错误处理**：增强了错误处理和重试机制，提高了可靠性。
- **日志记录**：记录下载、重试和错误的详细日志。
- **数据库**：存储下载历史，避免重复下载，并高效地管理重试任务。

## 环境要求

- Python 3.x（推荐使用最新版本）
- `requests`（用于HTTP请求）
- `beautifulsoup4`（用于HTML解析）
- `cloudscraper`（可选，用于绕过Cloudflare）
- `DrissionPage`（可选，用于无头浏览）


## 安装

1. **克隆项目**：
    ```bash
    git clone https://github.com/Pixiv-/Pixiv.git
    cd Pixiv
    ```

2. **创建虚拟环境**（可选，推荐）：
    ```bash
    python3 -m venv venv
    source venv/bin/activate   # Windows系统使用: venv\Scripts\activate
    ```

4. **设置Pixiv Cookie**：
    脚本需要使用Pixiv的cookie进行身份验证。你需要手动将cookie填入脚本中的以下行：

    ```python
    PIXIV_COOKIE = "your_cookie_here"   # 替换为你的Pixiv cookie
    ```

    或者，你也可以将Pixiv的cookie设置为环境变量：
    ```bash
    export PIXIV_COOKIE="your_cookie_here"   # Windows系统使用: set PIXIV_COOKIE=your_cookie_here
    ```

    如果你不知道如何获取cookie，可以通过浏览器的开发者工具（在“应用程序”标签下，查看“Cookies”部分）找到它。

## 使用方法

运行脚本，使用以下命令：

```bash
python pixiv_download_script.py
