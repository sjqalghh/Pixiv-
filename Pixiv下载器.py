from DrissionPage import Chromium, ChromiumOptions
import requests
import re
import os
import time
import random
import sys
import threading
import keyboard  # 跨平台按键监听库

# ========== 配置 ==========
SAVE_ROOT = r"D:\pixiv"   # 保存根目录
REQUEST_TIMEOUT = 15      # 请求超时（秒）
MIN_DELAY, MAX_DELAY = 0, 0  # 去掉下载等待
PAUSE_KEY = 'space'       # 使用空格键暂停/恢复

# ========== 辅助函数 ==========
def to_original_url(thumb_url: str) -> str:
    """根据缩略图 URL 重组为 img-original 的可能原图 URL（优先 .jpg）。"""
    if not thumb_url:
        return thumb_url
    m = re.search(r'https://i\.pximg\.net/.+?/(?:img-master|custom-thumb)/img/(.+?)_p0', thumb_url)
    if m:
        path = m.group(1)
        return f'https://i.pximg.net/img-original/img/{path}_p0.jpg'
    m2 = re.search(r'https://i\.pximg\.net/c/.+?/img/(.+?)_p0', thumb_url)
    if m2:
        path = m2.group(1)
        return f'https://i.pximg.net/img-original/img/{path}_p0.jpg'
    m3 = re.search(r'/img/(.+?)_p0', thumb_url)
    if m3:
        path = m3.group(1)
        return f'https://i.pximg.net/img-original/img/{path}_p0.jpg'
    return thumb_url

def extract_author_name(alt_text: str) -> str:
    """从 alt 属性中提取作者名字。"""
    match = re.search(r"-\s*(.*?)的插画$", alt_text)
    if match:
        return match.group(1).strip()
    return "未知作者"

def sanitize_filename(name: str) -> str:
    """清除Windows文件名中的非法字符"""
    return re.sub(r'[\\/:*?"<>|]', '_', name)

def download_image_silent(url: str, filename: str, headers: dict) -> bool:
    """默认下载jpg，失败静默改为png再尝试"""
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200 and r.content:
            with open(filename, 'wb') as f:
                f.write(r.content)
            print(f"✅ 成功保存：{filename}")
            return True
        if url.endswith('.jpg'):
            alt_url = url.replace('.jpg', '.png')
            r2 = requests.get(alt_url, headers=headers, timeout=REQUEST_TIMEOUT)
            if r2.status_code == 200 and r2.content:
                with open(filename, 'wb') as f:
                    f.write(r2.content)
                print(f"✅ 成功保存：{filename}")
                return True
    except Exception as e:
        print(f"⚠️ 下载异常：{e}")
        if url.endswith('.jpg'):
            try:
                alt_url = url.replace('.jpg', '.png')
                r2 = requests.get(alt_url, headers=headers, timeout=REQUEST_TIMEOUT)
                if r2.status_code == 200 and r2.content:
                    with open(filename, 'wb') as f:
                        f.write(r2.content)
                    print(f"✅ 成功保存：{filename}")
                    return True
            except Exception:
                pass
    return False

def read_downloaded_images(file_path: str):
    """读取已下载图片文件列表"""
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return set(f.read().splitlines())
    return set()

def write_downloaded_image(file_path: str, image_path: str):
    """记录已下载的图片到文件"""
    with open(file_path, 'a', encoding='utf-8') as f:
        f.write(image_path + '\n')

def is_image_downloaded(image_path: str, downloaded_images: set) -> bool:
    """检查图片是否已下载"""
    return image_path in downloaded_images

# ========== 暂停与恢复功能 ==========
is_paused = False

def pause_resume_listener():
    global is_paused
    while True:
        keyboard.wait(PAUSE_KEY)  # 等待按下空格键
        is_paused = not is_paused
        if is_paused:
            print("⏸️ 爬取已暂停，按空格键恢复...")
        else:
            print("▶️ 爬取恢复...")
        time.sleep(0.5)  # 防止快速重复触发

# ========== 主程序 ==========
def main():
    global is_paused
    tag_name = input("请输入要爬取的角色名（如 HuTao、Raiden、Furina 等）：").strip()
    page_input = input("请输入要爬取的页码（单页如 P=1，表示从第一页开始）：").strip()
    
    try:
        page_num = int(page_input.split('=')[-1].strip())
    except ValueError:
        print("输入页码无效")
        return
    
    range_input = input(f"请输入在第 {page_num} 页爬取的图片范围（例如：1-10，表示从第1张到第10张）：").strip()
    
    try:
        start, end = map(int, range_input.split('-'))
        if start <= 0 or end < start:
            print("输入的图片范围无效")
            return
    except ValueError:
        print("输入的图片范围格式不正确")
        return

    # 在用户输入完成并验证后启动暂停/恢复监听线程
    threading.Thread(target=pause_resume_listener, daemon=True).start()
    print(f"ℹ️ 按空格键可暂停/恢复爬取...")

    co = ChromiumOptions()
    co.headless(True)  # ✅ 启用无头模式
    co.set_argument('--window-size=1920,1080')
    co.set_argument('--blink-settings=imagesEnabled=true')
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-dev-shm-usage')
    co.set_local_port(9222)

    try:
        browser = Chromium(co)
    except Exception as e:
        print(f"无法连接 Chromium：{e}")
        return

    save_dir = os.path.join(SAVE_ROOT, tag_name)
    os.makedirs(save_dir, exist_ok=True)

    downloaded_images_file = os.path.join(save_dir, 'downloaded_images.txt')
    downloaded_images = read_downloaded_images(downloaded_images_file)

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Referer': 'https://www.pixiv.net/',
    }

    page_url = f"https://www.pixiv.net/tags/{tag_name}/illustrations?p={page_num}"
    print(f"\n🔗 正在下载第 {page_num} 页：{page_url}")

    try:
        tab = browser.new_tab(page_url)
        tab.wait(5)
    except Exception:
        print("输入页数不存在")
        try:
            tab.close()
        except Exception:
            pass
        return

    seen = set()
    seq = start
    found_any = False

    print("🔄 开始滚动页面以加载所有图片...")
    for _ in range(5):
        tab.scroll.down(1200)
        time.sleep(random.uniform(1.0, 2.0))
        tab.scroll.right(1200)
        time.sleep(random.uniform(1.0, 2.0))

    for i in range(start, end + 1):
        try:
            img_ele = tab.ele(
                f'x:/html/body/div[1]/div/div[2]/div[5]/div[1]/div[3]/div[3]/section/div[2]/div[1]/ul/li[{i}]/div/div[1]/div/a/div[1]/div/img'
            )
        except Exception:
            img_ele = None

        if not img_ele:
            continue

        try:
            thumb_url = img_ele.attr('src') or img_ele.attr('data-src') or img_ele.attr('data-original') or ''
            if not thumb_url:
                # 如果 src 为空，强制触发懒加载
                tab.run_js(f"""
                    var img = document.querySelectorAll('ul li')[{i-1}].querySelector('img');
                    if(img){{
                        var dsrc = img.getAttribute('data-src') || img.getAttribute('data-original');
                        if(dsrc) img.src = dsrc;
                    }}
                """)
                time.sleep(0.3)
                thumb_url = img_ele.attr('src') or img_ele.attr('data-src') or img_ele.attr('data-original') or ''
        except Exception:
            thumb_url = ''

        if not thumb_url:
            continue

        found_any = True

        if thumb_url.startswith('//'):
            thumb_url = 'https:' + thumb_url
        if thumb_url.startswith('/'):
            thumb_url = 'https://www.pixiv.net' + thumb_url

        alt_text = img_ele.attr('alt') or ""
        author_name = extract_author_name(alt_text)
        safe_author = sanitize_filename(author_name)

        orig_url = to_original_url(thumb_url)
        if not orig_url:
            continue

        if orig_url in seen or is_image_downloaded(orig_url, downloaded_images):
            continue
        seen.add(orig_url)

        filename = os.path.join(save_dir, f"{page_num}_{seq}_{safe_author}.jpg")
        seq += 1

        download_image_silent(orig_url, filename, headers)
        write_downloaded_image(downloaded_images_file, filename)

        while is_paused:
            time.sleep(0.5)

    try:
        tab.close()
    except Exception:
        pass

    if not found_any:
        print("输入页数不存在")
        return

    print("\n✅ 指定图片范围全部处理完毕。")

if __name__ == "__main__":
    main()