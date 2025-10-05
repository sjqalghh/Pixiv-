Pixiv 图片下载器
概述
本项目是一个基于Python的网络爬虫，旨在根据用户指定的标签、页码和图片范围从Pixiv下载图片。它使用DrissionPage库进行浏览器自动化，支持下载原始质量的图片（优先下载.jpg格式，回退到.png）。脚本包含跨平台的暂停/恢复功能，通过空格键触发，允许用户动态控制抓取过程。
功能

基于标签的搜索：下载与特定标签相关的图片（例如角色名如HuTao、Raiden、Furina）。
页面和范围选择：指定要抓取的页面编号和要从该页面下载的图片范围。
原始图片下载：将缩略图URL转换为原始图片URL以下载高质量图片。
暂停/恢复功能：通过按空格键暂停或恢复抓取，支持Windows、macOS和Linux平台。
防重复下载：跟踪已下载的图片以避免重复下载。
错误处理：对网络问题、无效输入和缺失图片进行健壮处理。
无头浏览器：使用无头模式的Chromium进行高效抓取。
用户可使用作者所提供的Drissionpage配置配置文件更改缩启用的浏览器对象

先决条件
运行脚本前，请确保已安装以下内容：

Python：版本3.8或更高。
Chromium/Chrome浏览器：DrissionPage需要此浏览器来自动化Web交互。
Python库：
DrissionPage：用于浏览器自动化。
requests：用于HTTP请求下载图片。
keyboard：用于跨平台按键检测（暂停/恢复功能）。
其他标准库：re、os、time、random、sys、threading。



安装

克隆仓库：
git clone https://github.com/yourusername/pixiv-image-downloader.git
cd pixiv-image-downloader


安装Python依赖：
pip install DrissionPage requests keyboard

注意：keyboard库在Linux或macOS上可能需要root/admin权限才能检测按键。如有必要，请使用sudo运行安装命令：
sudo pip install keyboard


确保已安装Chromium/Chrome：

脚本使用DrissionPage库，需要基于Chromium的浏览器（例如Google Chrome或Chromium）。
在Windows/macOS上，通常可以自动检测到Chrome。在Linux上，确保安装了chromium-browser或google-chrome：sudo apt-get install chromium-browser  # 适用于Debian/Ubuntu




设置保存目录：

脚本默认将图片保存到D:\pixiv（可在SAVE_ROOT变量中配置）。
如果需要，请更新demo11.py中的SAVE_ROOT路径为系统上的有效目录。



使用方法

运行脚本：
python demo11.py


提供输入：

标签名称：输入Pixiv标签（例如HuTao、Raiden、Furina）来搜索相关图片。
页码：指定要抓取的页面（例如P=1表示第一页）。
图片范围：输入要从指定页面下载的图片范围（例如1-10表示从第1张到第10张）。

输入示例：
请输入要爬取的角色名（如 HuTao、Raiden、Furina 等）：荧
请输入要爬取的页码（单页如 P=1，表示从第一页开始）：P=1
请输入在第 1 页爬取的图片范围（例如：1-10，表示从第1张到第10张）：1-10


暂停/恢复：

提供输入后，按空格键暂停或恢复抓取过程。
控制台将在暂停时显示⏸️ 爬取已暂停，按空格键恢复...，在恢复时显示▶️ 爬取恢复...。


输出：

图片保存到SAVE_ROOT/tag_name/（例如D:\pixiv\荧\）。
文件名格式为：{page_number}_{sequence}_{author_name}.jpg。
保存目录中的downloaded_images.txt文件会跟踪已下载的图片以防止重复下载。



配置
您可以修改demo11.py中的以下变量来自定义脚本：

SAVE_ROOT：保存图片的目录（默认：D:\pixiv）。
REQUEST_TIMEOUT：HTTP请求超时时间（默认：15秒）。
PAUSE_KEY：触发暂停/恢复的按键（默认：space表示空格键）。

示例
要从"荧"标签的第一页下载第1到第10张图片：

运行脚本。
输入：
标签：荧
页面：P=1
范围：1-10


脚本将：
打开无头Chromium浏览器。
导航到https://www.pixiv.net/tags/荧/illustrations?p=1。
下载第1到第10张图片，保存到D:\pixiv\荧\。
允许通过按空格键暂停/恢复。

注意事项

Pixiv访问：确保您有稳定的网络连接并能访问Pixiv。某些图片可能需要Pixiv账户或特定权限。
速率限制：脚本不包含下载间隔（MIN_DELAY、MAX_DELAY设置为0）。请谨慎操作，避免使Pixiv服务器过载，这可能导致IP被封禁。
无头模式：脚本以无头模式运行以提高效率，但可以通过在demo11.py中设置co.headless(False)修改为非无头模式。
错误处理：脚本处理常见错误（例如网络问题、无效页面），但请确保输入有效以避免中断。

依赖项

Python>=3.8
DrissionPage：浏览器自动化库。
requests：HTTP请求处理。
keyboard：跨平台按键检测。
标准Python库：re、os、time、random、sys、threading。

免责声明
本项目仅供教育和个人使用。作者和贡献者不对任何滥用本软件的行为负责，包括但不限于：

违反Pixiv服务条款或任何其他平台的政策。
未经授权下载或分发受版权保护的内容。
因使用此脚本而产生的任何法律或道德后果。

重要提示：

请尊重Pixiv的服务条款，仅下载您有权使用的內容。
不要将此脚本用于商业目的或损害Pixiv服务器（例如通过过多请求）。
作者与Pixiv无关，也不认可或鼓励任何非法活动。

请负责任地使用此工具，并自行承担风险。
许可证
本项目采用MIT许可证。详情请见LICENSE文件。
贡献
欢迎贡献！请：

派生仓库。
为您的功能或bug修复创建新分支。
提交带有清晰更改描述的拉取请求。

联系方式
如有问题或疑问，请在GitHub仓库上提交issue。