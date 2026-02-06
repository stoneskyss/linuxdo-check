"""
cron: 0 */6 * * *
new Env("Linux.Do 签到")
"""

import os
import random
import time
import functools
import re
from urllib.parse import urlparse
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate
from curl_cffi import requests
from bs4 import BeautifulSoup


# ----------------------------
# Retry Decorator
# ----------------------------
def retry_decorator(retries=3, min_delay=5, max_delay=10):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries - 1:
                        logger.error(f"函数 {func.__name__} 最终执行失败: {str(e)}")
                    logger.warning(
                        f"函数 {func.__name__} 第 {attempt + 1}/{retries} 次尝试失败: {str(e)}"
                    )
                    if attempt < retries - 1:
                        sleep_s = random.uniform(min_delay, max_delay)
                        logger.info(f"将在 {sleep_s:.2f}s 后重试")
                        time.sleep(sleep_s)
            return None

        return wrapper

    return decorator


# ----------------------------
# Env & Config
# ----------------------------
# ⚠️ 不要主动 pop DISPLAY：你在 Actions 用 xvfb 时需要 DISPLAY
# os.environ.pop("DISPLAY", None)
os.environ.pop("DYLD_LIBRARY_PATH", None)

USERNAME = os.environ.get("LINUXDO_USERNAME") or os.environ.get("USERNAME")
PASSWORD = os.environ.get("LINUXDO_PASSWORD") or os.environ.get("PASSWORD")

BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in [
    "false",
    "0",
    "off",
]

# Headless：默认 true（Actions 更稳）；你要 xvfb + 非无头就设 HEADLESS=false
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]

MAX_TOPICS = int(os.environ.get("MAX_TOPICS", "50"))
MIN_COMMENT_PAGES = int(os.environ.get("MIN_COMMENT_PAGES", "5"))
MAX_COMMENT_PAGES = int(os.environ.get("MAX_COMMENT_PAGES", "10"))
PAGE_GROW = int(os.environ.get("PAGE_GROW", "10"))

LIKE_PROB = float(os.environ.get("LIKE_PROB", "0.3"))

# 推进楼层增长：大步滚动
SCROLL_MIN = int(os.environ.get("SCROLL_MIN", "1000"))
SCROLL_MAX = int(os.environ.get("SCROLL_MAX", "1600"))

MAX_LOOP_FACTOR = float(os.environ.get("MAX_LOOP_FACTOR", "10"))

# 阅读阈值（你要求默认写死 5/20，同时支持 env 覆盖）
MIN_READ_STAY = float(os.environ.get("MIN_READ_STAY", "5"))
READ_STATE_TIMEOUT = float(os.environ.get("READ_STATE_TIMEOUT", "20"))

# 阅读节奏（按你那份脚本）
READ_SCROLL_MIN = int(os.environ.get("READ_SCROLL_MIN", "200"))
READ_SCROLL_MAX = int(os.environ.get("READ_SCROLL_MAX", "500"))
READ_INTERVAL_MIN = float(os.environ.get("READ_INTERVAL_MIN", "1"))
READ_INTERVAL_MAX = float(os.environ.get("READ_INTERVAL_MAX", "3"))
READ_TIME_MIN = float(os.environ.get("READ_TIME_MIN", "5"))
READ_TIME_MAX = float(os.environ.get("READ_TIME_MAX", "15"))

# near-bottom
NEAR_BOTTOM_GAP = int(os.environ.get("NEAR_BOTTOM_GAP", "140"))
BOTTOM_EXTRA_STAY_MIN = float(os.environ.get("BOTTOM_EXTRA_STAY_MIN", "6"))
BOTTOM_EXTRA_STAY_MAX = float(os.environ.get("BOTTOM_EXTRA_STAY_MAX", "12"))

# timings 批量大小（借鉴扩展）
TIMINGS_MIN_REQ = int(os.environ.get("TIMINGS_MIN_REQ", "8"))
TIMINGS_MAX_REQ = int(os.environ.get("TIMINGS_MAX_REQ", "20"))
TIMINGS_MIN_MS = int(os.environ.get("TIMINGS_MIN_MS", "800"))
TIMINGS_MAX_MS = int(os.environ.get("TIMINGS_MAX_MS", "3000"))
TIMINGS_BASE_DELAY_MS = int(os.environ.get("TIMINGS_BASE_DELAY_MS", "2500"))
TIMINGS_RAND_DELAY_MS = int(os.environ.get("TIMINGS_RAND_DELAY_MS", "800"))

GOTIFY_URL = os.environ.get("GOTIFY_URL")
GOTIFY_TOKEN = os.environ.get("GOTIFY_TOKEN")
SC3_PUSH_KEY = os.environ.get("SC3_PUSH_KEY")
WXPUSH_URL = os.environ.get("WXPUSH_URL")
WXPUSH_TOKEN = os.environ.get("WXPUSH_TOKEN")

LIST_URL = "https://linux.do/latest"
HOME_FOR_COOKIE = "https://linux.do/"
LOGIN_URL = "https://linux.do/login"
SESSION_URL = "https://linux.do/session"
CSRF_URL = "https://linux.do/session/csrf"

POST_CONTENT_CSS = "div.post__regular.regular.post__contents.contents"


class LinuxDoBrowser:
    def __init__(self) -> None:
        from sys import platform

        if platform.startswith("linux"):
            platformIdentifier = "X11; Linux x86_64"
        elif platform == "darwin":
            platformIdentifier = "Macintosh; Intel Mac OS X 10_15_7"
        elif platform == "win32":
            platformIdentifier = "Windows NT 10.0; Win64; x64"
        else:
            platformIdentifier = "X11; Linux x86_64"

        co = ChromiumOptions().incognito(True).set_argument("--no-sandbox")

        # ✅ GitHub Actions 非无头时，最容易卡在 9222 连接失败
        # 解决：给每次运行一个“独立 user-data-dir + 随机调试端口”
        tmp_profile = f"/tmp/linuxdo_dp_profile_{int(time.time())}_{random.randint(1000,9999)}"
        try:
            if hasattr(co, "set_user_data_path"):
                co.set_user_data_path(tmp_profile)
            else:
                co.set_argument(f'--user-data-dir={tmp_profile}')
        except Exception:
            co.set_argument(f'--user-data-dir={tmp_profile}')

        dp_port = int(os.environ.get("DP_PORT", str(random.randint(9223, 9299))))
        # DrissionPage 有的版本支持 set_local_port
        try:
            if hasattr(co, "set_local_port"):
                co.set_local_port(dp_port)
            else:
                co.set_argument(f'--remote-debugging-port={dp_port}')
        except Exception:
            co.set_argument(f'--remote-debugging-port={dp_port}')

        co.headless(HEADLESS)

        # 避免后台节流
        co.set_argument("--disable-background-timer-throttling")
        co.set_argument("--disable-backgrounding-occluded-windows")
        co.set_argument("--disable-renderer-backgrounding")

        # 一些常见稳定参数
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--no-first-run")
        co.set_argument("--no-default-browser-check")

        co.set_user_agent(
            f"Mozilla/5.0 ({platformIdentifier}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )

        self.browser = Chromium(co)
        self.page = self.browser.new_tab()

        # HTTP session：用于登录、connect info（不用于 timings）
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )

        # timings 统计
        self.timings_sent = 0
        self.timings_ok = 0
        self.timings_fail = 0

    # ----------------------------
    # Headers
    # ----------------------------
    def _api_headers(self):
        return {
            "User-Agent": self.session.headers.get("User-Agent"),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": LOGIN_URL,
            "Origin": "https://linux.do",
        }

    def _html_headers(self):
        return {
            "User-Agent": self.session.headers.get("User-Agent"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": HOME_FOR_COOKIE,
        }

    # ----------------------------
    # CSRF + Login
    # ----------------------------
    def _get_csrf_token(self) -> str:
        self.session.get(
            HOME_FOR_COOKIE,
            headers=self._html_headers(),
            impersonate="chrome136",
            allow_redirects=True,
            timeout=30,
        )

        resp_csrf = self.session.get(
            CSRF_URL,
            headers=self._api_headers(),
            impersonate="chrome136",
            allow_redirects=True,
            timeout=30,
        )
        ct = (resp_csrf.headers.get("content-type") or "").lower()
        if resp_csrf.status_code != 200 or "application/json" not in ct:
            head = (resp_csrf.text or "")[:200]
            raise RuntimeError(
                f"CSRF not JSON. status={resp_csrf.status_code}, ct={ct}, head={head}"
            )
        data = resp_csrf.json()
        csrf = data.get("csrf")
        if not csrf:
            raise RuntimeError(f"CSRF JSON missing token keys: {list(data.keys())}")
        return csrf

    def login(self):
        logger.info("开始登录")
        logger.info("获取 CSRF token...")

        try:
            csrf_token = self._get_csrf_token()
        except Exception as e:
            logger.error(f"获取 CSRF 失败：{e}")
            return False

        logger.info("正在登录...")

        headers = self._api_headers()
        headers.update(
            {
                "X-CSRF-Token": csrf_token,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
        )

        data = {"login": USERNAME, "password": PASSWORD, "timezone": "Asia/Shanghai"}

        try:
            resp_login = self.session.post(
                SESSION_URL,
                data=data,
                impersonate="chrome136",
                headers=headers,
                allow_redirects=True,
                timeout=30,
            )
            ct = (resp_login.headers.get("content-type") or "").lower()
            if "application/json" not in ct:
                logger.error(f"登录返回不是 JSON，head={resp_login.text[:200]}")
                return False

            response_json = resp_login.json()
            if response_json.get("error"):
                logger.error(f"登录失败: {response_json.get('error')}")
                return False

            logger.info("登录成功!")
        except Exception as e:
            logger.error(f"登录请求异常: {e}")
            return False

        self.print_connect_info()

        logger.info("同步 Cookie 到 DrissionPage...")
        cookies_dict = self.session.cookies.get_dict()
        dp_cookies = [
            {"name": name, "value": value, "domain": ".linux.do", "path": "/"}
            for name, value in cookies_dict.items()
        ]
        self.page.set.cookies(dp_cookies)

        logger.info("Cookie 设置完成，导航至主题列表页 /latest ...")
        self.page.get(LIST_URL)

        try:
            self.page.wait.ele("@id=main-outlet", timeout=25)
        except Exception:
            logger.warning("未等到 main-outlet，但继续尝试查找 topic link")

        ok = self._wait_any_topic_link(timeout=35)
        if not ok:
            logger.warning("未等到主题链接 a.raw-topic-link")
            logger.warning(f"url={self.page.url}")
            logger.warning((self.page.html or "")[:500])
            return True

        logger.info("主题列表已渲染，登录&页面加载完成")
        return True

    def _wait_any_topic_link(self, timeout=30) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            try:
                links = self.page.eles("css:a.raw-topic-link")
                if links and len(links) > 0:
                    return True
            except Exception:
                pass
            time.sleep(0.8)
        return False

    # ----------------------------
    # Topic/Posts helpers
    # ----------------------------
    def _post_count_in_dom(self, page) -> int:
        try:
            return int(page.run_js("""return document.querySelectorAll('[id^="post_"]').length;""") or 0)
        except Exception:
            return 0

    def _max_post_number_in_dom(self, page) -> int:
        try:
            return int(
                page.run_js(
                    """
                    let maxN = 0;
                    document.querySelectorAll('[id^="post_"]').forEach(el => {
                      const m = el.id.match(/^post_(\\d+)$/);
                      if (m) maxN = Math.max(maxN, parseInt(m[1], 10));
                    });
                    return maxN;
                    """
                )
                or 0
            )
        except Exception:
            return 0

    def wait_topic_posts_ready(self, page, timeout=60) -> bool:
        """
        不依赖 #post_1：任意 post 有正文即可
        """
        end = time.time() + timeout
        while time.time() < end:
            try:
                ok = page.run_js(
                    f"""
                    const posts = Array.from(document.querySelectorAll('[id^="post_"]'));
                    if (!posts.length) return false;
                    for (const p of posts) {{
                      const c = p.querySelector('{POST_CONTENT_CSS}');
                      if (!c) continue;
                      const t = (c.innerText || c.textContent || '').trim();
                      if (t.length > 0) return true;
                    }}
                    return false;
                    """
                )
                if ok:
                    cnt = self._post_count_in_dom(page)
                    mx = self._max_post_number_in_dom(page)
                    mn = int(
                        page.run_js(
                            """
                            let minN = 999999;
                            document.querySelectorAll('[id^="post_"]').forEach(el=>{
                              const m = el.id.match(/^post_(\\d+)$/);
                              if (m) minN = Math.min(minN, parseInt(m[1],10));
                            });
                            return (minN===999999)?0:minN;
                            """
                        )
                        or 0
                    )
                    logger.info(f"帖子流已渲染：dom_posts={cnt} range=post_{mn}..post_{mx}")
                    time.sleep(random.uniform(0.8, 1.6))
                    return True
            except Exception:
                pass
            time.sleep(0.6)

        logger.warning("未等到帖子流渲染完成（可能结构变化/加载慢/被拦截）")
        return False

    # ----------------------------
    # Blue-dot / read-state helpers
    # ----------------------------
    def _post_has_blue_dot(self, page, post_id: int) -> bool:
        try:
            return bool(
                page.run_js(
                    """
                    const pid = arguments[0];
                    const root = document.querySelector(`#post_${pid}`);
                    if (!root) return false;
                    const rs = root.querySelector('.topic-meta-data .read-state');
                    if (!rs) return false;
                    return !rs.classList.contains('read');
                    """,
                    post_id,
                )
            )
        except Exception:
            return False

    def _post_is_read_ui(self, page, post_id: int) -> bool:
        try:
            return bool(
                page.run_js(
                    """
                    const pid = arguments[0];
                    const root = document.querySelector(`#post_${pid}`);
                    if (!root) return false;
                    const rs = root.querySelector('.topic-meta-data .read-state');
                    if (!rs) return false;
                    return rs.classList.contains('read');
                    """,
                    post_id,
                )
            )
        except Exception:
            return False

    def _list_visible_posts_in_viewport(self, page):
        try:
            ids = page.run_js(
                """
                const els = Array.from(document.querySelectorAll('[id^="post_"]'));
                const v = [];
                for (const el of els) {
                  const r = el.getBoundingClientRect();
                  if (r.bottom < 0 || r.top > window.innerHeight) continue;
                  const m = el.id.match(/^post_(\\d+)$/);
                  if (m) v.push(parseInt(m[1], 10));
                }
                return v;
                """
            )
            if not ids:
                return []
            return [int(x) for x in ids]
        except Exception:
            return []

    # ----------------------------
    # Topic id / csrf from page
    # ----------------------------
    def _get_topic_id_from_url(self, page) -> int:
        """
        /t/topic/1564445/29  或 /t/topic/1564445
        """
        try:
            u = page.url or ""
            path = urlparse(u).path
            parts = [p for p in path.split("/") if p]
            # 期望: ['t','topic','1564445','29']
            for i in range(len(parts) - 1):
                if parts[i] == "topic" and parts[i + 1].isdigit():
                    return int(parts[i + 1])
            # 兜底：取 path 里的第一个长数字
            m = re.search(r"/topic/(\\d+)", path)
            if m:
                return int(m.group(1))
        except Exception:
            pass
        return 0

    def _get_csrf_from_page(self, page) -> str:
        try:
            token = page.run_js(
                """const el=document.querySelector('meta[name="csrf-token"]'); return el?el.getAttribute('content'):'';"""
            )
            return token or ""
        except Exception:
            return ""

    # ----------------------------
    # timings (IMPORTANT): must be sent via browser fetch
    # ----------------------------
    def _timings_sleep(self):
        ms = TIMINGS_BASE_DELAY_MS + random.randint(0, TIMINGS_RAND_DELAY_MS)
        time.sleep(ms / 1000.0)

    def _post_timings_via_browser_fetch(self, page, topic_id: int, post_ids):
        """
        在页面上下文里 fetch /topics/timings
        """
        if not topic_id or not post_ids:
            return False

        csrf = self._get_csrf_from_page(page)
        if not csrf:
            logger.warning("timings: 页面未取到 csrf-token，跳过本次")
            return False

        # 生成 timings 参数（按扩展逻辑随机 ms）
        timings = {}
        for pid in post_ids:
            timings[int(pid)] = random.randint(TIMINGS_MIN_MS, TIMINGS_MAX_MS)

        topic_time = sum(timings.values())
        # 允许 topic_time 有一点随机浮动更像真实
        topic_time = max(topic_time, random.randint(TIMINGS_MIN_MS * len(post_ids), TIMINGS_MAX_MS * len(post_ids)))

        # 拼 form body（timings[xx]=ms）
        body_pairs = []
        for pid, ms in timings.items():
            body_pairs.append(f"timings%5B{pid}%5D={ms}")  # timings[pid]
        body_pairs.append(f"topic_time={topic_time}")
        body_pairs.append(f"topic_id={topic_id}")
        body = "&".join(body_pairs)

        # 记录日志
        self.timings_sent += 1
        ref = page.url

        try:
            status = page.run_js(
                """
                const csrf = arguments[0];
                const body = arguments[1];
                const ref = arguments[2];
                return fetch("https://linux.do/topics/timings", {
                  method: "POST",
                  mode: "cors",
                  credentials: "include",
                  referrer: ref || "https://linux.do/",
                  headers: {
                    "accept": "*/*",
                    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "discourse-background": "true",
                    "discourse-logged-in": "true",
                    "discourse-present": "true",
                    "x-csrf-token": csrf,
                    "x-requested-with": "XMLHttpRequest",
                    "x-silence-logger": "true"
                  },
                  body
                }).then(r => r.status).catch(_ => -1);
                """,
                csrf,
                body,
                ref,
            )

            if int(status) == 200:
                self.timings_ok += 1
            else:
                self.timings_fail += 1

            logger.info(
                f"timings(fetch): status={status} topic_id={topic_id} posts={sorted(post_ids)} topic_time={topic_time} body={body}"
            )
            logger.info(f"timings: totals sent={self.timings_sent} ok={self.timings_ok} fail={self.timings_fail}")
            self._timings_sleep()
            return int(status) == 200
        except Exception as e:
            self.timings_fail += 1
            logger.warning(f"timings(fetch) exception: {e}")
            logger.info(f"timings: totals sent={self.timings_sent} ok={self.timings_ok} fail={self.timings_fail}")
            self._timings_sleep()
            return False

    # ----------------------------
    # Human-like read (use your scroll rhythm)
    # ----------------------------
    def _scroll_read_rhythm(self, page, duration_s: float):
        """
        按你那份脚本节奏：200~500px，1~3s，持续 duration
        """
        start = time.time()
        cnt = 0
        while time.time() - start < duration_s:
            dist = random.randint(READ_SCROLL_MIN, READ_SCROLL_MAX)
            try:
                page.run_js("window.scrollBy(0, arguments[0]);", dist)
            except Exception:
                pass
            cnt += 1

            # 触发一些事件更像真人
            try:
                page.run_js(
                    """
                    try { window.focus(); } catch(e) {}
                    try {
                      const ev = new MouseEvent('mousemove', {
                        clientX: 80 + Math.random()*600,
                        clientY: 80 + Math.random()*500
                      });
                      document.dispatchEvent(ev);
                    } catch(e) {}
                    try { window.dispatchEvent(new Event('scroll')); } catch(e) {}
                    """
                )
            except Exception:
                pass

            time.sleep(random.uniform(READ_INTERVAL_MIN, READ_INTERVAL_MAX))

            # 到底就停
            try:
                at_bottom = page.run_js(
                    """return (window.innerHeight + window.scrollY) >= document.body.offsetHeight - 120;"""
                )
            except Exception:
                at_bottom = False

            if at_bottom:
                break

        return cnt

    def _read_post_like_human(self, page, topic_id: int, post_id: int):
        """
        只读蓝点楼层：
        - scrollIntoView
        - 阅读滚动 5~15s
        - 视口内的 posts 做 timings 上报（batch）
        - UI 是否变 read 只做提示，不作为“是否计数”的硬条件
        """
        try:
            page.run_js(
                """
                const pid = arguments[0];
                const el = document.querySelector(`#post_${pid}`);
                if (el) el.scrollIntoView({behavior:'instant', block:'center'});
                """,
                post_id,
            )
        except Exception:
            pass

        read_s = random.uniform(READ_TIME_MIN, READ_TIME_MAX)
        read_s = max(read_s, MIN_READ_STAY)
        logger.info(f"👀 阅读未读楼层 post_{post_id}（阅读滚动≈{read_s:.1f}s）")

        self._scroll_read_rhythm(page, read_s)

        # 关键：提交 timings —— 用“当前视口内楼层”更接近真实（你抓包说视口内 5 个左右）
        vp = self._list_visible_posts_in_viewport(page)
        vp = [pid for pid in vp if pid >= 1]
        if vp:
            # batch 大小截断到 5~10 更像真实滚动
            k = min(len(vp), random.randint(4, 7))
            batch = vp[:k]
            self._post_timings_via_browser_fetch(page, topic_id, batch)

        # UI 状态：不强求一定变 read（你已经遇到“UI 不变但实际会发 timings”）
        if self._post_is_read_ui(page, post_id):
            return True

        # 给一点点 UI 同步等待（非必须）
        end = time.time() + min(READ_STATE_TIMEOUT, 6)
        while time.time() < end:
            if self._post_is_read_ui(page, post_id):
                return True
            time.sleep(0.5)

        logger.warning(
            f"⚠️ post_{post_id} 停留已达阈值但蓝点未消失（UI 未见 read-state.read；不一定代表未计阅读）"
        )
        return False

    # ----------------------------
    # Near-bottom
    # ----------------------------
    def _near_bottom(self, page, gap=140) -> bool:
        try:
            return bool(
                page.run_js(
                    """
                    const d = document.documentElement;
                    const y = window.scrollY || d.scrollTop || 0;
                    const maxY = Math.max(0, d.scrollHeight - window.innerHeight);
                    return (maxY - y) <= arguments[0];
                    """,
                    gap,
                )
            )
        except Exception:
            return False

    # ----------------------------
    # Browse replies (page grow)
    # ----------------------------
    def browse_replies_pages(self, page, min_pages=5, max_pages=10):
        if max_pages < min_pages:
            max_pages = min_pages
        target_pages = random.randint(min_pages, max_pages)
        logger.info(f"目标：浏览评论 {target_pages} 页（按楼层号增长计，PAGE_GROW={PAGE_GROW}）")

        self.wait_topic_posts_ready(page, timeout=60)

        topic_id = self._get_topic_id_from_url(page)
        if topic_id:
            logger.info(f"topic_id={topic_id} (from url)")

        pages_done = 0
        last_max_no = self._max_post_number_in_dom(page)
        last_cnt = self._post_count_in_dom(page)
        logger.info(f"初始：max_post_no={last_max_no}, dom_posts={last_cnt}")

        max_loops = int(target_pages * MAX_LOOP_FACTOR + 20)

        seen_read_attempts = set()

        for i in range(max_loops):
            # 1) 大步滚动推进楼层增长
            scroll_distance = random.randint(SCROLL_MIN, SCROLL_MAX)
            logger.info(f"[loop {i+1}] 向下滚动 {scroll_distance}px 浏览评论...")
            try:
                page.run_js("window.scrollBy(0, arguments[0]);", scroll_distance)
            except Exception:
                pass

            time.sleep(random.uniform(1.0, 1.8))

            # 2) 视口内只读蓝点（最多 1~3 个）
            vp = self._list_visible_posts_in_viewport(page)
            unread = [pid for pid in vp if self._post_has_blue_dot(page, pid)]
            unread = [pid for pid in unread if pid not in seen_read_attempts]

            if unread and topic_id:
                k = min(len(unread), random.randint(1, 3))
                for pid in unread[:k]:
                    seen_read_attempts.add(pid)
                    self._read_post_like_human(page, topic_id, pid)

            # 3) 页数判定
            cur_max_no = self._max_post_number_in_dom(page)
            cur_cnt = self._post_count_in_dom(page)

            if cur_max_no - last_max_no >= PAGE_GROW:
                pages_done += 1
                logger.success(
                    f"✅ 第 {pages_done}/{target_pages} 页：max_post_no {last_max_no} -> {cur_max_no}（dom_posts={cur_cnt}）"
                )
                last_max_no = cur_max_no
                last_cnt = cur_cnt

            # 4) near-bottom：额外阅读滚动（促发加载/自然 timings）
            if self._near_bottom(page, gap=NEAR_BOTTOM_GAP):
                extra = random.uniform(BOTTOM_EXTRA_STAY_MIN, BOTTOM_EXTRA_STAY_MAX)
                logger.info(f"[loop {i+1}] 接近底部，额外阅读滚动≈{extra:.1f}s")
                self._scroll_read_rhythm(page, extra)

                # near-bottom 再补一次 timings（用视口）
                if topic_id:
                    vp2 = self._list_visible_posts_in_viewport(page)
                    if vp2:
                        k2 = min(len(vp2), random.randint(4, 7))
                        self._post_timings_via_browser_fetch(page, topic_id, vp2[:k2])

            # 5) 达标退出
            if pages_done >= target_pages:
                logger.success("🎉 已达到目标评论页数，结束浏览")
                return True

            # 6) 到底退出
            try:
                at_bottom = page.run_js(
                    "return (window.scrollY + window.innerHeight) >= (document.body.scrollHeight - 5);"
                )
            except Exception:
                at_bottom = False

            if at_bottom:
                logger.success("已到达页面底部，结束浏览")
                if cur_max_no <= (min_pages * PAGE_GROW + 5):
                    logger.info(f"主题较短（max_post_no≈{cur_max_no}），放宽最小页数要求，视为完成")
                    return True
                return pages_done >= min_pages

        logger.warning("达到最大循环次数仍未完成目标页数（可能加载慢/主题很短/被拦截）")
        return pages_done >= min_pages

    # ----------------------------
    # Browse from latest list
    # ----------------------------
    def click_topic(self):
        if not self.page.url.startswith("https://linux.do/latest"):
            self.page.get(LIST_URL)

        if not self._wait_any_topic_link(timeout=35):
            logger.error("未找到 a.raw-topic-link（主题标题链接）")
            logger.error(f"当前URL: {self.page.url}")
            logger.error((self.page.html or "")[:500])
            return False

        topic_links = self.page.eles("css:a.raw-topic-link")
        if not topic_links:
            logger.error("主题链接列表为空")
            logger.error(f"当前URL: {self.page.url}")
            logger.error((self.page.html or "")[:500])
            return False

        count = min(MAX_TOPICS, len(topic_links))
        logger.info(f"发现 {len(topic_links)} 个主题帖，随机选择 {count} 个进行浏览")

        for a in random.sample(topic_links, count):
            href = a.attr("href")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://linux.do" + href
            self.click_one_topic(href)

        return True

    @retry_decorator()
    def click_one_topic(self, topic_url):
        new_page = self.browser.new_tab()
        try:
            new_page.get(topic_url)
            self.wait_topic_posts_ready(new_page, timeout=60)
            time.sleep(random.uniform(1.0, 2.0))

            if random.random() < LIKE_PROB:
                self.click_like(new_page)

            ok = self.browse_replies_pages(
                new_page,
                min_pages=MIN_COMMENT_PAGES,
                max_pages=MAX_COMMENT_PAGES,
            )
            if not ok:
                logger.warning("本主题未达到最小评论页数目标（可能帖子很短/到底/加载慢）")
        finally:
            try:
                new_page.close()
            except Exception:
                pass

    # ----------------------------
    # Like
    # ----------------------------
    def click_like(self, page):
        try:
            like_button = page.ele(".discourse-reactions-reaction-button")
            if like_button:
                logger.info("找到未点赞的帖子，准备点赞")
                like_button.click()
                logger.info("点赞成功")
                time.sleep(random.uniform(1, 2))
            else:
                logger.info("帖子可能已经点过赞了")
        except Exception as e:
            logger.error(f"点赞失败: {str(e)}")

    # ----------------------------
    # Connect info
    # ----------------------------
    def print_connect_info(self):
        logger.info("获取连接信息")
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        resp = self.session.get(
            "https://connect.linux.do/",
            headers=headers,
            impersonate="chrome136",
            allow_redirects=True,
            timeout=30,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.select("table tr")
        info = []
        for row in rows:
            cells = row.select("td")
            if len(cells) >= 3:
                project = cells[0].text.strip()
                current = cells[1].text.strip() if cells[1].text.strip() else "0"
                requirement = cells[2].text.strip() if cells[2].text.strip() else "0"
                info.append([project, current, requirement])

        print("--------------Connect Info-----------------")
        print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))

    # ----------------------------
    # Notifications
    # ----------------------------
    def send_notifications(self, browse_enabled):
        status_msg = f"✅每日登录成功: {USERNAME}"
        if browse_enabled:
            status_msg += (
                f" + 浏览完成(话题<= {MAX_TOPICS} 个, 评论{MIN_COMMENT_PAGES}-{MAX_COMMENT_PAGES}页, "
                f"PAGE_GROW={PAGE_GROW}, HEADLESS={HEADLESS}, timings ok={self.timings_ok}/{self.timings_sent})"
            )

        if GOTIFY_URL and GOTIFY_TOKEN:
            try:
                response = requests.post(
                    f"{GOTIFY_URL}/message",
                    params={"token": GOTIFY_TOKEN},
                    json={"title": "LINUX DO", "message": status_msg, "priority": 1},
                    timeout=10,
                )
                response.raise_for_status()
                logger.success("消息已推送至Gotify")
            except Exception as e:
                logger.error(f"Gotify推送失败: {str(e)}")
        else:
            logger.info("未配置Gotify环境变量，跳过通知发送")

        if SC3_PUSH_KEY:
            match = re.match(r"sct(\d+)t", SC3_PUSH_KEY, re.I)
            if not match:
                logger.error("❌ SC3_PUSH_KEY格式错误，未获取到UID，无法使用Server酱³推送")
                return
            uid = match.group(1)
            url = f"https://{uid}.push.ft07.com/send/{SC3_PUSH_KEY}"
            params = {"title": "LINUX DO", "desp": status_msg}

            attempts = 5
            for attempt in range(attempts):
                try:
                    response = requests.get(url, params=params, timeout=10)
                    response.raise_for_status()
                    logger.success(f"Server酱³推送成功: {response.text}")
                    break
                except Exception as e:
                    logger.error(f"Server酱³推送失败: {str(e)}")
                    if attempt < attempts - 1:
                        sleep_time = random.randint(180, 360)
                        logger.info(f"将在 {sleep_time} 秒后重试...")
                        time.sleep(sleep_time)

        if WXPUSH_URL and WXPUSH_TOKEN:
            try:
                response = requests.post(
                    f"{WXPUSH_URL}/wxsend",
                    headers={
                        "Authorization": WXPUSH_TOKEN,
                        "Content-Type": "application/json",
                    },
                    json={"title": "LINUX DO", "content": status_msg},
                    timeout=10,
                )
                response.raise_for_status()
                logger.success(f"wxpush 推送成功: {response.text}")
            except Exception as e:
                logger.error(f"wxpush 推送失败: {str(e)}")
        else:
            logger.info("未配置 WXPUSH_URL 或 WXPUSH_TOKEN，跳过通知发送")

    # ----------------------------
    # Run
    # ----------------------------
    def run(self):
        try:
            login_res = self.login()
            if not login_res:
                logger.warning("登录失败，后续任务可能无法进行")

            if BROWSE_ENABLED:
                click_topic_res = self.click_topic()
                if not click_topic_res:
                    logger.error("点击主题失败，程序终止")
                    return
                logger.info("完成浏览任务（含评论浏览）")

            self.send_notifications(BROWSE_ENABLED)
        finally:
            try:
                self.page.close()
            except Exception:
                pass
            try:
                self.browser.quit()
            except Exception:
                pass


if __name__ == "__main__":
    if not USERNAME or not PASSWORD:
        print("Please set LINUXDO_USERNAME/LINUXDO_PASSWORD (or USERNAME/PASSWORD)")
        raise SystemExit(1)

    l = LinuxDoBrowser()
    l.run()
