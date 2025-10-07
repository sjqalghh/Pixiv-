#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修正版 — Pixiv 真实爬取下载脚本（修复AJAX解析）
主要修复：
- 修复AJAX JSON解析逻辑，正确提取图片URL
- 改进缩略图到原图的转换
- 增强错误处理和日志
- 修复重试逻辑，避免不必要的重试
- 修复任务队列满的问题，支持大规模批量下载
- 增强网络错误处理和重试机制
- 修复多页下载时序列号连续递增的问题
"""

import os
import re
import sys
import time
import csv
import uuid
import json
import random
import hashlib
import logging
import sqlite3
import queue
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Optional, Dict

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Optional libs
try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except Exception:
    HAS_CLOUDSCRAPER = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except Exception:
    HAS_BS4 = False

try:
    from DrissionPage import Chromium, ChromiumOptions
    HAS_DRISSION = True
except Exception:
    HAS_DRISSION = False

# ---------------- CONFIG ----------------
TEST_BASE_URL = "https://www.pixiv.net"
SAVE_ROOT = "./pixiv_real_data"
DB_PATH = os.path.join(SAVE_ROOT, "simulator.db")
CSV_PATH = os.path.join(SAVE_ROOT, "download_report.csv")
FAILED_LOG = os.path.join(SAVE_ROOT, "failed_downloads.log")
LOG_PATH = os.path.join(SAVE_ROOT, "simulator.log")
REQRESP_LOG = os.path.join(SAVE_ROOT, "reqresp_log.csv")

REQUEST_TIMEOUT = 30  # 增加超时时间
HEAD_TIMEOUT = 10
MAX_DOWNLOAD_RETRIES = 6
BACKOFF_BASE = 0.5
DOWNLOAD_WORKERS = 8
TAB_WORKERS = 4
MAX_PAGES_TO_EXPAND = 8

RETRY_TOTAL = 5  # 增加重试次数
RETRY_STATUS_FORCELIST = [429, 500, 502, 503, 504, 520, 521, 522, 523, 524]
POOL_MAXSIZE = 1000  # 增大队列大小以支持批量下载

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0"
]

# 从环境读取 Cookie
PIXIV_COOKIE = os.environ.get("PIXIV_COOKIE", "")

CAPTURE_REQRESP = True

# ---------------- Logging ----------------
os.makedirs(SAVE_ROOT, exist_ok=True)
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s',
                    handlers=[logging.FileHandler(LOG_PATH, encoding='utf-8'), logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("pixiv_simulator_real")


# ---------------- DB Manager ----------------
class DBManager:
    def __init__(self, path: str):
        self.path = path
        self._init()

    def _migrate_db_schema(self, conn):
        try:
            c = conn.cursor()
            c.execute("PRAGMA table_info(images)")
            columns = [row[1] for row in c.fetchall()]
            if 'md5' not in columns:
                logger.info("⚙️ 检测到旧版数据库，正在升级表结构以添加 md5 列...")
                c.execute("ALTER TABLE images ADD COLUMN md5 TEXT DEFAULT '';")
                conn.commit()
                logger.info("✅ 数据库结构升级成功（已添加 md5 列）")
        except Exception as e:
            logger.warning("数据库结构检测或迁移失败: %s", e)

    def _init(self):
        conn = sqlite3.connect(self.path, check_same_thread=False)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS images (
                url TEXT PRIMARY KEY,
                saved_path TEXT,
                status INTEGER,
                size INTEGER,
                ts INTEGER
            );
        """)
        conn.commit()
        self._migrate_db_schema(conn)
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_md5 ON images(md5);")
            conn.commit()
        except Exception:
            pass
        conn.close()

    def exists(self, url: str) -> bool:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT 1 FROM images WHERE url = ?", (url,))
        r = c.fetchone()
        conn.close()
        return bool(r)

    def insert(self, url: str, saved_path: str, status: int, size: int, md5: Optional[str]):
        conn = sqlite3.connect(self.path, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute("ALTER TABLE images ADD COLUMN md5 TEXT DEFAULT '';")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("INSERT OR REPLACE INTO images(url,saved_path,status,size,md5,ts) VALUES(?,?,?,?,?,?)",
                      (url, saved_path, status, size, md5 or '', int(time.time())))
        except Exception:
            c.execute("INSERT OR REPLACE INTO images(url,saved_path,status,size,ts) VALUES(?,?,?,?,?)",
                      (url, saved_path, status, size, int(time.time())))
        conn.commit()
        conn.close()

    def find_by_md5(self, md5: str) -> Optional[Tuple[str, str]]:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute("SELECT url,saved_path FROM images WHERE md5 = ?", (md5,))
            r = c.fetchone()
        except Exception:
            r = None
        conn.close()
        return r


# ---------------- Utilities ----------------
def sanitize_filename(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', s)


def init_csv(csv_path: str):
    if not os.path.exists(csv_path):
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(['timestamp', 'url', 'status_code', 'size_bytes', 'time_s', 'save_path'])


def init_reqresp_log(path: str):
    if not os.path.exists(path):
        with open(path, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(['ts', 'request_id', 'method', 'url', 'req_headers', 'status', 'resp_headers', 'bytes', 'time_s'])


def md5_of_file(path: str, block_size: int = 65536) -> str:
    h = hashlib.md5()
    with open(path, 'rb') as f:
        while True:
            b = f.read(block_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ---------------- Session factory ----------------
def create_session(user_agent: Optional[str] = None, use_cloudscraper: bool = False):
    ua = user_agent or random.choice(USER_AGENTS)
    
    # 增强的headers
    headers = {
        'User-Agent': ua,
        'Referer': 'https://www.pixiv.net/',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
    }
    
    if use_cloudscraper and HAS_CLOUDSCRAPER:
        logger.info("使用 cloudscraper (UA=%s)", ua[:50] + "...")
        try:
            # 创建cloudscraper会话，增加更多配置选项
            scraper = cloudscraper.create_scraper(
                browser={
                    'custom': ua,
                    'browser': 'chrome',
                    'platform': 'windows',
                    'mobile': False
                },
                delay=10,
                captcha={}
            )
            scraper.headers.update(headers)
            if PIXIV_COOKIE:
                scraper.headers['Cookie'] = PIXIV_COOKIE
            return scraper
        except Exception as e:
            logger.warning("cloudscraper创建失败，回退到requests: %s", e)
            use_cloudscraper = False

    # 使用requests作为备选
    s = requests.Session()
    s.headers.update(headers)
    if PIXIV_COOKIE:
        s.headers['Cookie'] = PIXIV_COOKIE

    # 增强的重试策略
    retry = Retry(
        total=RETRY_TOTAL, 
        backoff_factor=1.0, 
        status_forcelist=RETRY_STATUS_FORCELIST,
        allowed_methods=frozenset(["HEAD", "GET", "OPTIONS", "POST"]),
        respect_retry_after_header=True
    )
    
    adapter = HTTPAdapter(
        max_retries=retry, 
        pool_connections=POOL_MAXSIZE, 
        pool_maxsize=POOL_MAXSIZE,
        pool_block=False
    )
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    
    return s


# ---------------- Thumbnail -> original candidates ----------------
def to_original_url(thumb_url: str) -> str:
    if not thumb_url:
        return thumb_url

    # 处理正方形缩略图
    m_square = re.search(r'https://i\.pximg\.net/.+?/img-master/img/(.+?)_p\d+_square1200\.jpg', thumb_url)
    if m_square:
        path = m_square.group(1)
        return f'https://i.pximg.net/img-original/img/{path}_p0.jpg'

    # 处理自定义缩略图
    m_custom = re.search(r'https://i\.pximg\.net/.+?/custom-thumb/img/(.+?)_p\d+_custom1200\.jpg', thumb_url)
    if m_custom:
        path = m_custom.group(1)
        return f'https://i.pximg.net/img-original/img/{path}_p0.jpg'

    # 原有逻辑
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


def generate_candidates_from_thumb(thumb_url: str, max_pages: int = MAX_PAGES_TO_EXPAND) -> List[str]:
    if not thumb_url:
        return []

    if thumb_url.startswith('//'):
        thumb_url = 'https:' + thumb_url
    if thumb_url.startswith('/'):
        thumb_url = urllib.parse.urljoin(TEST_BASE_URL, thumb_url)

    candidates = []
    original_url = to_original_url(thumb_url)
    if original_url and original_url != thumb_url:
        candidates.append(original_url)
        if original_url.endswith('.jpg'):
            candidates.append(original_url.replace('.jpg', '.png'))
        elif original_url.endswith('.png'):
            candidates.append(original_url.replace('.png', '.jpg'))

    # 多页逻辑
    m = re.search(r'(.+?)_p(\d+)\.(jpg|png|webp|gif)$', thumb_url, re.I)
    if m:
        base = m.group(1)
        for i in range(0, max_pages):
            candidates.append(f"{base}_p{i}.jpg")
            candidates.append(f"{base}_p{i}.png")
    else:
        # 处理带日期路径的URL
        m2 = re.search(r'/img-master/img/(\d+/\d+/\d+/\d+/\d+/\d+/\d+_p\d+)', thumb_url)
        if m2:
            basepath = m2.group(1)
            for i in range(0, max_pages):
                candidates.append(f"https://i.pximg.net/img-original/img/{basepath.replace(f'_p{i}', f'_p{i}')}.jpg")
                candidates.append(f"https://i.pximg.net/img-original/img/{basepath.replace(f'_p{i}', f'_p{i}')}.png")
        else:
            candidates.append(thumb_url)

    seen = set()
    out = []
    for c in candidates:
        if c not in seen:
            out.append(c)
            seen.add(c)
    return out


# ---------------- Request/Response capture ----------------
def capture_reqresp(request_id: str, method: str, url: str, req_headers: dict,
                    status: Optional[int], resp_headers: dict, bytes_count: int, elapsed: float):
    if not CAPTURE_REQRESP:
        return
    try:
        with open(REQRESP_LOG, 'a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow([
                time.strftime('%Y-%m-%d %H:%M:%S'), request_id, method, url,
                json.dumps({k: req_headers.get(k) for k in ['User-Agent', 'Referer', 'Cookie'] if req_headers.get(k)}),
                status if status else '',
                json.dumps({k: resp_headers.get(k) for k in ['Content-Length', 'Content-Type', 'Server'] if resp_headers.get(k)}),
                bytes_count, f"{elapsed:.3f}"
            ])
    except Exception:
        logger.exception("Failed to write reqresp log")


# ---------------- Download pipeline (with resume) ----------------
def safe_write_with_resume(tmp_path: str, response, existing: int = 0) -> Tuple[bool, int]:
    try:
        mode = 'ab' if existing and os.path.exists(tmp_path) else 'wb'
        written = existing
        with open(tmp_path, mode) as f:
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                f.write(chunk)
                written += len(chunk)
        return True, written
    except Exception:
        logger.exception("write failed")
        return False, 0


def attempt_download_with_resume(session, url: str, save_path: str, referer: Optional[str] = None) -> Tuple[bool, Optional[int], int, float]:
    headers = dict(session.headers) if hasattr(session, 'headers') else {}
    headers['Referer'] = referer or 'https://www.pixiv.net/'

    request_id = str(uuid.uuid4())
    tmp_path = save_path + ".!tmp"
    expected_len = None

    existing = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0

    start_head = time.time()
    try:
        h = session.head(url, headers=headers, timeout=HEAD_TIMEOUT, allow_redirects=True)
        head_elapsed = time.time() - start_head
        if getattr(h, 'status_code', None) in (200, 206) and 'content-length' in h.headers:
            try:
                expected_len = int(h.headers.get('content-length'))
            except Exception:
                expected_len = None
        capture_reqresp(request_id, "HEAD", url, headers, getattr(h, 'status_code', None), getattr(h, 'headers', {}) or {}, 0, head_elapsed)
    except Exception:
        capture_reqresp(request_id, "HEAD", url, headers, None, {}, 0, time.time() - start_head)
        expected_len = None

    start = time.time()
    try:
        get_headers = dict(headers)
        if existing and expected_len and existing < expected_len:
            get_headers['Range'] = f"bytes={existing}-"
        r = session.get(url, headers=get_headers, stream=True, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    except Exception:
        elapsed = time.time() - start
        capture_reqresp(request_id, "GET", url, headers, None, {}, 0, elapsed)
        return False, None, 0, elapsed

    status = getattr(r, 'status_code', None)
    if status not in (200, 206):
        elapsed = time.time() - start
        capture_reqresp(request_id, "GET", url, headers, status, getattr(r, 'headers', {}) or {}, 0, elapsed)
        try:
            r.close()
        except:
            pass
        return False, status, 0, elapsed

    if status == 200 and existing:
        try:
            os.remove(tmp_path)
            existing = 0
        except:
            pass

    ok, written = safe_write_with_resume(tmp_path, r, existing)
    elapsed = time.time() - start
    resp_headers = getattr(r, 'headers', {}) or {}
    capture_reqresp(request_id, "GET", url, headers, status, resp_headers, written, elapsed)
    try:
        r.close()
    except:
        pass

    if not ok:
        return False, status, written, elapsed

    total_expected = None
    if 'content-range' in resp_headers:
        cr = resp_headers.get('content-range')
        m = re.search(r'/(\d+)', cr or '')
        if m:
            try:
                total_expected = int(m.group(1))
            except:
                total_expected = None
    elif expected_len is not None:
        total_expected = expected_len

    final_size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else written
    if total_expected and final_size != total_expected:
        return False, status, final_size, elapsed

    try:
        os.replace(tmp_path, save_path)
        return True, status, final_size, elapsed
    except Exception:
        try:
            if os.path.exists(save_path):
                os.remove(save_path)
            os.replace(tmp_path, save_path)
            return True, status, final_size, elapsed
        except Exception:
            logger.exception("Failed to move tmp to final")
            return False, status, final_size, elapsed


def download_with_backoff(session, url: str, save_path: str, referer: Optional[str] = None):
    candidates = []
    lower = url.lower()
    if lower.endswith('.jpg'):
        candidates = [url, url[:-4] + '.png']
    elif lower.endswith('.png'):
        candidates = [url, url[:-4] + '.jpg']
    else:
        candidates = [url]

    last_status = None
    for candidate in candidates:
        for attempt in range(1, MAX_DOWNLOAD_RETRIES + 1):
            ok, status, size, elapsed = attempt_download_with_resume(session, candidate, save_path, referer)
            if ok:
                return True, status, size, elapsed
            last_status = status
            wait = BACKOFF_BASE * (2 ** (attempt - 1)) + random.random() * 0.6
            time.sleep(wait)
    return False, last_status, 0, 0.0


# ---------------- 修复的AJAX JSON解析 ----------------
def fetch_candidates_via_ajax(session, tag: str, page: int, debug: bool = True) -> List[Tuple[str, str]]:
    """
    修复的AJAX解析函数，正确提取Pixiv搜索结果的图片URL
    """
    out = []
    try:
        enc_tag = urllib.parse.quote(tag)
        urls = [
            f"{TEST_BASE_URL}/ajax/search/artworks/{enc_tag}?p={page}&s_mode=s_tag_full&lang=zh",
            f"{TEST_BASE_URL}/ajax/search/artworks/{enc_tag}?word={enc_tag}&p={page}&lang=zh",
            f"{TEST_BASE_URL}/ajax/search/artworks/{enc_tag}?p={page}&s_mode=s_tag&lang=zh",
            f"{TEST_BASE_URL}/ajax/search/artworks/{enc_tag}?p={page}&lang=zh"
        ]
        
        for url in urls:
            try:
                start = time.time()
                r = session.get(url, timeout=REQUEST_TIMEOUT)
                elapsed = time.time() - start
                capture_reqresp(str(uuid.uuid4()), 'GET', url, dict(session.headers) if hasattr(session, 'headers') else {}, 
                              getattr(r, 'status_code', None), getattr(r, 'headers', {}) or {}, 
                              len(r.content) if getattr(r, 'content', None) else 0, elapsed)

                if debug:
                    logger.info("=== AJAX DEBUG ===")
                    logger.info("URL: %s", url)
                    logger.info("Status: %s", getattr(r, 'status_code', None))
                    logger.info("Resp len: %d", len(getattr(r, 'text', '')))

                if r.status_code != 200:
                    logger.debug("AJAX url %s returned %s", url, r.status_code)
                    continue
                    
                try:
                    j = r.json()
                except Exception as e:
                    logger.debug("AJAX url %s JSON解析失败: %s", url, e)
                    continue

                # 修复的JSON解析逻辑
                if isinstance(j, dict) and j.get('error') is False:
                    body = j.get('body', {})
                    
                    # 提取illustManga数据
                    illust_data = body.get('illustManga', {}).get('data', [])
                    if not illust_data:
                        # 尝试其他可能的字段
                        illust_data = body.get('illust', {}).get('data', [])
                    
                    if not illust_data:
                        # 尝试其他可能的字段名
                        illust_data = body.get('works', [])
                    
                    logger.info("找到 %d 个作品", len(illust_data))
                    
                    for item in illust_data:
                        if not isinstance(item, dict):
                            continue
                            
                        # 提取缩略图URL
                        thumb_url = item.get('url', '')
                        if not thumb_url:
                            # 尝试其他可能的字段名
                            thumb_url = item.get('imageUrl', '') or item.get('thumb', '')
                            
                        if not thumb_url:
                            continue
                            
                        # 提取作者信息
                        user_info = item.get('user', {})
                        author_name = user_info.get('name', '') or item.get('userName', '') or item.get('author', '未知作者')
                        
                        # 确保URL格式正确
                        if thumb_url.startswith('//'):
                            thumb_url = 'https:' + thumb_url
                        if thumb_url.startswith('/'):
                            thumb_url = urllib.parse.urljoin(TEST_BASE_URL, thumb_url)
                            
                        out.append((thumb_url, author_name))
                        
                        if debug and len(out) <= 3:  # 只打印前3个用于调试
                            logger.info("提取到图片: %s, 作者: %s", thumb_url, author_name)

                if out:
                    logger.info("AJAX成功提取 %d 个图片URL", len(out))
                    break
                    
            except Exception as e:
                logger.debug("AJAX请求失败 %s: %s", url, e)
                continue
                
    except Exception as e:
        logger.exception('AJAX整体异常: %s', e)
        
    return out


# ---------------- 增强的网络请求函数 ----------------
def robust_request(session, url: str, method: str = 'GET', max_retries: int = 3, **kwargs):
    """
    增强的网络请求函数，具有更好的错误处理和重试机制
    """
    for attempt in range(max_retries):
        try:
            response = session.request(method, url, **kwargs)
            return response
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
            logger.warning("请求 %s 连接失败 (尝试 %d/%d): %s", url, attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.random()
                logger.info("等待 %.2f 秒后重试...", wait_time)
                time.sleep(wait_time)
            else:
                logger.error("请求 %s 最终失败: %s", url, e)
                raise
        except Exception as e:
            logger.warning("请求 %s 失败 (尝试 %d/%d): %s", url, attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                raise
    return None


# ---------------- 其他函数保持不变 ----------------
def extract_author_name(alt_text: str) -> str:
    if not alt_text:
        return "未知作者"
    match = re.search(r"-\s*(.*?)的插画$", alt_text)
    if match:
        return match.group(1).strip()
    m2 = re.search(r'by\s+(.+)$', alt_text, re.I)
    if m2:
        return m2.group(1).strip()
    return "未知作者"


def extract_images_by_xpath(tab, start_index: int, end_index: int) -> List[Tuple[str, str]]:
    images_data = []
    for i in range(start_index, end_index + 1):
        try:
            xpath = f"/html/body/div[1]/div/div[2]/div[5]/div[1]/div[3]/div[3]/section/div[2]/div[1]/ul/li[{i}]/div/div[1]/div/a/div[1]/div/img"
            img_ele = tab.ele(f'x:{xpath}')
            if not img_ele:
                continue

            thumb_url = img_ele.attr('src') or img_ele.attr('data-src') or img_ele.attr('data-original') or ''
            if not thumb_url:
                tab.run_js(f"""
    var li = document.querySelectorAll('ul li')[{i-1}];
    if(li){{
        var img = li.querySelector('img');
        if(img){{
            var d = img.getAttribute('data-src') || img.getAttribute('data-original');
            if(d) img.src = d;
        }}
    }}
""")
                time.sleep(0.25)
                thumb_url = img_ele.attr('src') or img_ele.attr('data-src') or img_ele.attr('data-original') or ''

            if not thumb_url:
                continue

            if thumb_url.startswith('//'):
                thumb_url = 'https:' + thumb_url
            if thumb_url.startswith('/'):
                thumb_url = urllib.parse.urljoin(TEST_BASE_URL, thumb_url)

            alt_text = img_ele.attr('alt') or ""
            author_name = extract_author_name(alt_text)
            images_data.append((thumb_url, author_name))
        except Exception as e:
            logger.debug("Failed to extract image at index %s: %s", i, e)
            continue
    return images_data


class ProgressStats:
    def __init__(self):
        self.lock = threading.Lock()
        self.total_queued = 0
        self.success = 0
        self.failed = 0
        self.skipped = 0  # 新增：记录跳过的任务数

    def inc_total(self, n=1):
        with self.lock:
            self.total_queued += n

    def inc_success(self):
        with self.lock:
            self.success += 1

    def inc_failed(self):
        with self.lock:
            self.failed += 1
            
    def inc_skipped(self):
        with self.lock:
            self.skipped += 1

    def snapshot(self):
        with self.lock:
            return self.total_queued, self.success, self.failed, self.skipped


def download_worker_loop(task_q: queue.Queue, session, db: DBManager, csv_path: str, referer: str, stats: ProgressStats):
    while True:
        try:
            url, save_path = task_q.get(timeout=6)
        except queue.Empty:
            return
        try:
            if db.exists(url):
                logger.debug("URL already in DB, skip: %s", url)
                stats.inc_skipped()
                task_q.task_done()
                continue

            ok, status, size, elapsed = download_with_backoff(session, url, save_path, referer)
            ts = time.strftime('%Y-%m-%d %H:%M:%S')
            with open(csv_path, 'a', newline='', encoding='utf-8') as f:
                csv.writer(f).writerow([ts, url, status if status else '', size, f"{elapsed:.3f}", save_path])

            if ok:
                md5 = ''
                try:
                    md5 = md5_of_file(save_path)
                    dup = db.find_by_md5(md5)
                    if dup:
                        logger.info("Duplicate by md5: %s already stored at %s; removing %s", url, dup[1], save_path)
                        try:
                            os.remove(save_path)
                        except:
                            pass
                        db.insert(url, dup[1], status or 200, size, md5)
                    else:
                        db.insert(url, save_path, status or 200, size, md5)
                except Exception:
                    logger.exception("md5 calculation/insert error")
                    db.insert(url, save_path, status or 200, size, None)

                logger.info("Saved %s (status=%s size=%d md5=%s)", save_path, status, size, (md5[:8] if md5 else ''))
                stats.inc_success()
            else:
                logger.warning("Failed %s status=%s", url, status)
                with open(FAILED_LOG, 'a', encoding='utf-8') as f:
                    f.write(f"{ts} FAILED {url} status={status}\n")
                stats.inc_failed()
        except Exception:
            logger.exception("Download worker exception")
            stats.inc_failed()
        finally:
            task_q.task_done()


def run_full_simulation(tag: str, pages: List[int], per_page_range: Tuple[int, int], tab_workers: int = TAB_WORKERS,
                        download_workers: int = DOWNLOAD_WORKERS, use_cloudscraper: bool = False):
    logger.info("开始完整模拟: tag=%s pages=%s range=%s tabs=%d downloads=%d cloud=%s",
                tag, pages, per_page_range, tab_workers, download_workers, use_cloudscraper)

    os.makedirs(SAVE_ROOT, exist_ok=True)
    init_csv(CSV_PATH)
    if CAPTURE_REQRESP:
        init_reqresp_log(REQRESP_LOG)

    # 清空失败日志，避免历史记录干扰
    try:
        with open(FAILED_LOG, 'w', encoding='utf-8') as f:
            f.write('')
    except Exception:
        pass

    db = DBManager(DB_PATH)
    session = create_session(user_agent=random.choice(USER_AGENTS), use_cloudscraper=use_cloudscraper)
    
    # 使用更大的队列以支持批量下载
    task_q = queue.Queue(maxsize=POOL_MAXSIZE)
    save_dir_root = os.path.join(SAVE_ROOT, sanitize_filename(tag))
    os.makedirs(save_dir_root, exist_ok=True)

    page_paths = [f"/tags/{urllib.parse.quote(tag)}/illustrations?p={p}" for p in pages]

    # 修改：使用字典来存储每页的缩略图，而不是一个扁平列表
    thumbs_by_page = {page: [] for page in pages}

    # 启动 DrissionPage（若可用）
    if HAS_DRISSION:
        try:
            co = ChromiumOptions()
            co.headless(True)
            co.set_argument('--no-sandbox')
            co.set_argument('--disable-dev-shm-usage')
            co.set_argument('--disable-blink-features=AutomationControlled')
            co.set_user_agent(random.choice(USER_AGENTS))
            browser = Chromium(co)
            logger.info("DrissionPage Chromium started")
        except Exception as e:
            logger.warning("Failed to start Chromium: %s. Falling back to requests parsing.", e)
            browser = None
    else:
        browser = None

    def process_page_with_tab(page_path: str, page_num: int):
        page_url = urllib.parse.urljoin(TEST_BASE_URL, page_path)
        try:
            tab = browser.new_tab(page_url)
            time.sleep(2)
            for _ in range(8):
                try:
                    tab.scroll.down(1200)
                except:
                    pass
                time.sleep(random.uniform(0.4, 0.9))
            start_idx, end_idx = per_page_range
            images_data = extract_images_by_xpath(tab, start_idx, end_idx)
            try:
                tab.close()
            except:
                pass
            result = []
            for thumb_url, author in images_data:
                result.append((thumb_url, None, author))
            return result
        except Exception:
            logger.exception("Tab parse failed for %s", page_path)
            return []

    if browser:
        with ThreadPoolExecutor(max_workers=min(tab_workers, max(1, len(page_paths)))) as ex:
            # 修改：传递页码信息
            futures = {ex.submit(process_page_with_tab, p, page): (page, p) for page, p in zip(pages, page_paths)}
            for fut in as_completed(futures):
                try:
                    page_results = fut.result(timeout=90)
                    page_num, page_path = futures[fut]
                    for thumb_url, original_direct, author in page_results:
                        thumbs_by_page[page_num].append((thumb_url, original_direct, author))
                except Exception:
                    logger.exception("Future error")
        try:
            browser.close()
        except:
            pass
    else:
        logger.warning("DrissionPage not available, trying AJAX JSON endpoints first (faster & JS-free)")
        for page, page_path in zip(pages, page_paths):
            # 先尝试 AJAX
            try:
                ajax_candidates = fetch_candidates_via_ajax(session, tag, page, debug=True)
                if ajax_candidates:
                    for thumb, author in ajax_candidates:
                        thumbs_by_page[page].append((thumb, None, author))
                    logger.info("AJAX: 从第 %d 页收集到 %d 个缩略图", page, len(ajax_candidates))
                    continue
                else:
                    logger.warning("AJAX: 第 %d 页没有找到图片", page)
            except Exception as e:
                logger.debug("AJAX failed for page %s: %s", page_path, e)

            # AJAX 失败则回退到静态 HTML 解析
            page_url = urllib.parse.urljoin(TEST_BASE_URL, page_path)
            try:
                logger.info("Fallback: GET %s", page_url)
                # 使用增强的请求函数
                r = robust_request(session, page_url, timeout=REQUEST_TIMEOUT)
                if r is None:
                    logger.error("无法获取页面 %s", page_url)
                    continue
                    
                capture_reqresp(str(uuid.uuid4()), 'GET', page_url, dict(session.headers) if hasattr(session, 'headers') else {}, 
                              getattr(r, 'status_code', None), getattr(r, 'headers', {}) or {}, 
                              len(r.content) if getattr(r, 'content', None) else 0, 0.0)
                if r.status_code == 200 and HAS_BS4:
                    soup = BeautifulSoup(r.text, 'html.parser')
                    thumbs = []
                    authors = []

                    for item in soup.select('ul li'):
                        img = item.find('img')
                        if not img:
                            continue
                        src = img.get('data-src') or img.get('data-original') or img.get('src') or ''
                        if not src or any(x in src for x in ('_next/static', 's.pximg.net', '/static/', 'icon-', '.svg', 'avatar')):
                            continue
                        alt_text = img.get('alt') or ''
                        author = extract_author_name(alt_text)
                        if src.startswith('//'):
                            src = 'https:' + src
                        if src.startswith('/'):
                            src = urllib.parse.urljoin(TEST_BASE_URL, src)
                        thumbs.append(src)
                        authors.append(author)

                    while len(authors) < len(thumbs):
                        authors.append('未知作者')

                    for i, thumb in enumerate(thumbs):
                        thumbs_by_page[page].append((thumb, None, authors[i]))
                    logger.info("Fallback HTML: 从 %s 收集到 %d 个缩略图", page_url, len(thumbs))
                else:
                    logger.warning("Page %s returned status %s", page_url, r.status_code)
            except Exception as e:
                logger.exception("Failed to GET page %s: %s", page_url, e)

    # 计算总候选图片数
    total_thumbs = sum(len(thumbs) for thumbs in thumbs_by_page.values())
    logger.info("从所有页面共收集到 %d 个候选图片", total_thumbs)

    # 将候选URL加入下载队列 - 修改：每页独立计数
    downloaded_count = 0
    stats = ProgressStats()

    # 先收集所有任务，然后批量添加到队列
    all_tasks = []
    
    # 对每页独立处理
    for page in pages:
        page_thumbs = thumbs_by_page[page]
        page_seq = 0  # 每页重新开始计数
        
        for i, (thumb, original_direct, author) in enumerate(page_thumbs, start=1):
            page_seq += 1
            candidates = []
            if original_direct:
                candidates.append(original_direct)
            if thumb:
                candidates.extend(generate_candidates_from_thumb(thumb, max_pages=MAX_PAGES_TO_EXPAND))

            chosen = None
            for c in candidates:
                if not db.exists(c):
                    chosen = c
                    break
            if not chosen:
                continue

            parsed = urllib.parse.urlparse(chosen)
            m = re.search(r'\.(jpg|png|gif|webp)$', parsed.path, re.I)
            ext = '.jpg' if not m else '.' + m.group(1).lower()

            safe_author = sanitize_filename(author)
            # 修改：文件名格式为 "页码_页内序号_标签_作者.扩展名"
            fname = f"{page}_{page_seq}_{sanitize_filename(tag)}_{safe_author}{ext}"
            save_path = os.path.join(save_dir_root, fname)

            all_tasks.append((chosen, save_path))
            downloaded_count += 1
            stats.inc_total(1)
            logger.debug("准备下载 %s -> %s", chosen, save_path)

    if downloaded_count == 0:
        logger.warning("没有新图片需要下载。可能所有图片都已存在于数据库中。")
        return

    # 批量添加任务到队列，使用更智能的队列管理
    logger.info("开始将 %d 个任务添加到下载队列...", len(all_tasks))
    
    # 启动下载工作线程
    threads = []
    for _ in range(max(1, download_workers)):
        t = threading.Thread(target=download_worker_loop, args=(task_q, session, db, CSV_PATH, TEST_BASE_URL, stats), daemon=True)
        t.start()
        threads.append(t)

    # 批量添加任务，使用更长的超时时间和重试机制
    for task in all_tasks:
        url, save_path = task
        added = False
        for attempt in range(10):  # 最多重试10次
            try:
                task_q.put((url, save_path), timeout=30)  # 更长的超时时间
                added = True
                break
            except queue.Full:
                logger.warning("任务队列已满，等待后重试 (尝试 %d/10)...", attempt + 1)
                # 等待一段时间让消费者处理一些任务
                time.sleep(5)
        
        if not added:
            logger.error("无法将任务添加到队列，跳过: %s", url)
            stats.inc_skipped()

    logger.info("所有任务已添加到队列，开始下载...")

    try:
        while True:
            total, succ, fail, skipped = stats.snapshot()
            remaining = total - succ - fail - skipped
            logger.info("进度: 总数=%d 成功=%d 失败=%d 跳过=%d 剩余=%d", total, succ, fail, skipped, remaining)
            if remaining <= 0:
                break
            time.sleep(5)
    except KeyboardInterrupt:
        logger.warning("用户中断，等待工作线程完成当前任务...")

    task_q.join()
    logger.info("所有下载完成。关闭工作线程。")
    for t in threads:
        t.join(timeout=0.1)

    logger.info("模拟完成。下载了 %d 张图片。CSV: %s DB: %s", downloaded_count, CSV_PATH, DB_PATH)

    retry_failed_urls_after_main_loop(session, db, csv_path=CSV_PATH, referer=TEST_BASE_URL)


def retry_failed_urls_after_main_loop(session, db: DBManager, csv_path: str, referer: str, max_retries: int = 3):
    # 添加检查：如果失败日志不存在，直接返回
    if not os.path.exists(FAILED_LOG):
        logger.info("没有失败日志需要重试。")
        return
        
    logger.info("读取失败日志进行重试...")
    urls = []
    with open(FAILED_LOG, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            try:
                idx = parts.index('FAILED')
                url = parts[idx + 1]
                urls.append(url)
            except Exception:
                continue

    uniq = list(dict.fromkeys(urls))
    
    # 关键修复：检查是否真的有待重试的URL
    actually_failed = []
    for url in uniq:
        if not db.exists(url):
            actually_failed.append(url)
        else:
            logger.debug("URL已在数据库中，跳过重试: %s", url)
    
    if not actually_failed:
        logger.info("所有失败URL已在主下载中成功处理，无需重试。")
        # 清空失败日志文件
        try:
            with open(FAILED_LOG, 'w', encoding='utf-8') as f:
                f.write("")
        except Exception:
            pass
        return

    logger.info("重试 %d 个真正失败的URL", len(actually_failed))
    
    for url in actually_failed:
        parsed = urllib.parse.urlparse(url)
        m = re.search(r'\.(jpg|png|gif|webp)$', parsed.path, re.I)
        ext = '.jpg' if not m else '.' + m.group(1).lower()
        fname = f"retry_{int(time.time())}_{sanitize_filename(os.path.basename(parsed.path))}"
        save_path_dir = os.path.join(SAVE_ROOT, "retries")
        os.makedirs(save_path_dir, exist_ok=True)
        save_file = os.path.join(save_path_dir, fname + ext)
        success = False
        for attempt in range(1, max_retries + 1):
            try:
                ok, status, size, elapsed = download_with_backoff(session, url, save_file, referer)
                ts = time.strftime('%Y-%m-%d %H:%M:%S')
                with open(csv_path, 'a', newline='', encoding='utf-8') as f:
                    csv.writer(f).writerow([ts, url, status if status else '', size, f"{elapsed:.3f}", save_file])
                if ok:
                    md5 = ''
                    try:
                        md5 = md5_of_file(save_file)
                        dup = db.find_by_md5(md5)
                        if dup:
                            try:
                                os.remove(save_file)
                            except:
                                pass
                            db.insert(url, dup[1], status or 200, size, md5)
                        else:
                            db.insert(url, save_file, status or 200, size, md5)
                    except Exception:
                        db.insert(url, save_file, status or 200, size, None)
                    logger.info("重试成功 %s", url)
                    success = True
                    break
                else:
                    logger.warning("重试尝试 %d 失败 %s status=%s", attempt, url, status)
            except Exception:
                logger.exception("重试 %s 时发生异常", url)
            time.sleep(1 + random.random())
        if not success:
            logger.warning("所有重试尝试都失败 %s", url)


def input_int(prompt, default):
    v = input(prompt).strip()
    if not v:
        return default
    try:
        return int(v)
    except:
        return default


if __name__ == "__main__":
    print("PIXIV REAL DOWNLOADER (修复AJAX解析版)")
    print("注意: 使用前请确保已设置 PIXIV_COOKIE 环境变量")
    if PIXIV_COOKIE:
        print("✅ 已检测到 PIXIV_COOKIE 环境变量")
    else:
        print("⚠️  未检测到 PIXIV_COOKIE 环境变量")

    tag = input("Tag/角色名 (如 HuTao、Raiden、Furina): ").strip() or "HuTao"
    pages_input = input("页码 (单页 '1' 或范围 '1-3' 或逗号 '1,3,5'): ").strip() or "1"

    pages = []
    if ',' in pages_input:
        pages = [int(x) for x in pages_input.split(',') if x.strip().isdigit()]
    elif '-' in pages_input:
        a, b = pages_input.split('-', 1)
        pages = list(range(int(a), int(b) + 1))
    else:
        try:
            pages = [int(pages_input)]
        except:
            pages = [1]

    rng = input("每页图片范围 (如 1-10): ").strip() or "1-10"
    try:
        a, b = map(int, rng.split('-'))
    except:
        a, b = 1, 10

    tabs = input_int(f"标签页工作数 (默认 {TAB_WORKERS}): ", TAB_WORKERS)
    dl_workers = input_int(f"下载工作数 (默认 {DOWNLOAD_WORKERS}): ", DOWNLOAD_WORKERS)
    cloud = input("使用cloudscraper绕过CloudFlare? (y/N): ").strip().lower().startswith('y')

    if cloud and not HAS_CLOUDSCRAPER:
        logger.warning("cloudscraper 未安装; 使用普通 requests 会话")
        cloud = False

    run_full_simulation(tag, pages, per_page_range=(a, b), tab_workers=tabs, download_workers=dl_workers, use_cloudscraper=cloud)