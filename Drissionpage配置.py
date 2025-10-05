from DrissionPage import ChromiumOptions

path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"  # 请改为你电脑内Chrome/Edge 可执行文件路径
ChromiumOptions().set_browser_path(path).save()
