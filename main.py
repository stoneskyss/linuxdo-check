# -*- coding: utf-8 -*-
"""
cron: 0 */6 * * *
new Env("Linux.Do 签到")
"""

import os
import random
import time
import functools
import re
import tempfile
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
            last_err = None
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_err = e
                    if attempt == retries - 1:
                        logger.error(f"函数 {func.__name__} 最终执行失败: {str(e)}")
                    else:
                        logger.warning(
                            f"函数 {func.__name__} 第 {attempt + 1}/{retries} 次尝试失败: {str(e)}"
                        )
                        sleep_s = random.uniform(min_delay, max_delay)
                        logger.info(
                            f"将在 {sleep_s:.2f}s 后重试 ({min_delay}-{max_delay}s 随机延迟)"
                        )
                        time.sleep(sleep_s)
            raise last_err

        return wrapper

    return decorator


# ----------------------------
# Env & Config
# ----------------------------
# 注意：不要强行 pop DISPLAY；你在 Actions + Xvfb 时需要它
os.environ.pop("DYLD_LIBRARY_PATH", None)

USERNAME = os.environ.get("LINUXDO_USERNAME") or os.environ.get("USERNAME")
PASSWORD = os.environ.get("LINUXDO_PASSWORD") or os.environ.get("PASSWORD")

BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in [
    "false",
    "0",
    "off",
]

# ✅ Actions + Xvfb 时可设 HEADLESS=false
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]

# 每次运行最多进入多少个话题帖
MAX_TOPICS = int(os.environ.get("MAX_TOPICS", "50"))

# 每个话题至少/最多浏览多少“页/批次”评论
MIN_COMMENT_PAGES = int(os.environ.get("MIN_COMMENT_PAGES", "5"))
MAX_COMMENT_PAGES = int(os.environ.get("MAX_COMMENT_PAGES", "10"))

# “翻一页评论”的判定：最大楼层号增长多少算 1 页
PAGE_GROW = int(os.environ.get("PAGE_GROW", "10"))

# 点赞概率（0~1）
LIKE_PROB = float(os.environ.get("LIKE_PROB", "0.3"))

# 大步滚动距离（推进楼层增长）
SCROLL_MIN = int(os.environ.get("SCROLL_MIN", "1000"))
SCROLL_MAX = int(os.environ.get("SCROLL_MAX", "1600"))

# ✅ 借鉴你“可用脚本”的滚动节奏（更像真人）
READ_SCROLL_MIN = int(os.environ.get("READ_SCROLL_MIN", "200"))
READ_SCROLL_MAX = int(os.environ.get("READ_SCROLL_MAX", "500"))
READ_INTERVAL_MIN = float(os.environ.get("READ_INTERVAL_MIN", "1"))
READ_INTERVAL_MAX = float(os.environ.get("READ_INTERVAL_MAX", "3"))

# ✅ 阅读停留（默认写死 5 / 20，你也可 env 覆盖）
MIN_READ_STAY = float(os.environ.get("MIN_READ_STAY", "5"))
READ_STATE_TIMEOUT = float(os.environ.get("READ_STATE_TIMEOUT", "20"))

# 接近底部判定
NEAR_BOTTOM_GAP = int(os.environ.get("NEAR_BOTTOM_GAP", "140"))
BOTTOM_EXTRA_STAY_MIN = float(os.environ.get("BOTTOM_EXTRA_STAY_MIN", "6"))
BOTTOM_EXTRA_STAY_MAX = float(os.environ.get("BOTTOM_EXTRA_STAY_MAX", "12"))

# ✅ timings（按你扩展 content.js 的默认值）
TIMINGS_MIN_REQ = int(os.environ.get("TIMINGS_MIN_REQ", "8"))
TIMINGS_MAX_REQ = int(os.environ.get("TIMINGS_MAX_REQ", "20"))
TIMINGS_MIN_MS = int(os.environ.get("TIMINGS_MIN_MS", "800"))
TIMINGS_MAX_MS = int(os.environ.get("TIMINGS_MAX_MS", "3000"))
TIMINGS_BASE_DELAY_MS = int(os.environ.get("TIMINGS_BASE_DELAY_MS", "2500"))
TIMINGS_RANDOM_DELAY_MS = int(os.environ.get("TIMINGS_RANDOM_DELAY_MS", "800"))

# ✅ 只对“仍有蓝点”的楼层做 timings（更稳）
ONLY_TIMINGS_UNREAD = os.environ.get("ONLY_TIMINGS_UNREAD", "true").strip().lower() not in [
    "false",
    "0",
    "off",
]

# DrissionPage 远程调试端口：避免 Actions 里 9222 冲突
DP_PORT = int(os.environ.get("DP_PORT", str(random.randint(20000, 40000))))

GOTIFY_URL = os.environ.get("GOTIFY_URL")
GOTIFY_TOKEN = os.environ.get("GOTIFY_TOKEN")
SC3_PUSH_KEY = os.environ.get("SC3_PUSH_KEY")
WXPUSH_URL = os.environ.get("WXPUSH_URL")
WXPUSH_TOKEN = os.environ.get("WXPUSH_TOKEN")

# 访问入口
LIST_URL = "https://linux.do/latest"
HOME_FOR_COOKIE = "https://linux.do/"
LOGIN_URL = "https://linux.do/login"
SESSION_URL = "https://linux.do/session"
CSRF_URL = "https://linux.do/session/csrf"
TIMINGS_URL = "https://linux.do/topics/timings"

# 帖子正文选择器（用于确认已渲染）
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

        co = ChromiumOptions().incognito(True)

        # ✅ 避免 9222 冲突 / user-data 冲突
        user_dir = tempfile.mkdtemp(prefix="dp_ud_")
        co.set_user_data_path(user_dir)
        co.set_local_port(DP_PORT)

        # ✅ 常见 Actions/容器参数
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-gpu")
        co.set_argument("--disable-blink-features=AutomationControlled")
        co.set_argument("--disable-background-timer-throttling")
        co.set_argument("--disable-backgrounding-occluded-windows")
        co.set_argument("--disable-renderer-backgrounding")

        # 无头控制
        co.headless(HEADLESS)
        if not HEADLESS:
            co.set_argument("--start-maximized")

        co.set_user_agent(
            f"Mozilla/5.0 ({platformIdentifier}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )

        self.browser = Chromium(co)
        self.page = self.browser.new_tab()

        # requests session 用于登录/通知，不用于 timings（timings 必须走浏览器上下文）
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
    # CSRF + Login (requests)
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

        # 同步 Cookie 到 DrissionPage
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
        ✅ 不依赖 #post_1：只要任意 post 有正文文本即视为 ready
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
        """
        蓝点判断：存在 .read-state 且不包含 class 'read' => 未读
        """
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
    # timings (按扩展 content.js 逻辑：在浏览器上下文 fetch + headers + form)
    # ----------------------------
    def _get_topic_id_and_csrf(self, page):
        """
        返回 (topic_id:int|None, csrf:str|None)
        topic_id：按你扩展里的方式从 pathname split 取第 4 段
        """
        try:
            data = page.run_js(
                """
                const csrfEl = document.querySelector('meta[name="csrf-token"]');
                const csrf = csrfEl ? csrfEl.getAttribute('content') : null;
                const parts = window.location.pathname.split('/').filter(Boolean);
                // 常见：/t/topic/1564445/29 => parts = ['t','topic','1564445','29']
                const tid = (parts.length >= 3) ? parseInt(parts[2], 10) : null;
                return {topic_id: tid, csrf: csrf, url: location.href};
                """
            )
            if not data:
                return None, None, None
            return data.get("topic_id"), data.get("csrf"), data.get("url")
        except Exception:
            return None, None, None

    def _post_timings_via_page_fetch(self, page, post_ids):
        """
        ✅ 模仿扩展：POST /topics/timings
        - headers: accept */*, x-csrf-token, discourse-present/background/logged-in, x-requested-with, x-silence-logger
        - body: timings[pid]=随机毫秒 + topic_time + topic_id
        - referrer: 当前 topic 页面 URL（更像真实）
        """
        post_ids = [int(x) for x in post_ids if isinstance(x, (int, str)) and str(x).isdigit()]
        post_ids = sorted(set(post_ids))
        if not post_ids:
            return None

        topic_id, csrf, ref_url = self._get_topic_id_and_csrf(page)
        if not topic_id or not csrf or not ref_url:
            logger.warning("timings(fetch): 无法获取 topic_id/csrf/ref_url，跳过")
            return None

        # 生成 timings 值（借鉴扩展：每楼随机 min~max）
        timings_map = {pid: random.randint(TIMINGS_MIN_MS, TIMINGS_MAX_MS) for pid in post_ids}
        topic_time = sum(timings_map.values())

        # 组装 body（按 form）
        body_pairs = []
        for pid, ms in timings_map.items():
            body_pairs.append(f"timings[{pid}]={ms}")
        body_pairs.append(f"topic_time={topic_time}")
        body_pairs.append(f"topic_id={topic_id}")
        body = "&".join(body_pairs)

        # 让每次请求大小更像扩展：如果传入 post_ids 过少，可“填充”到 1~N（但只在你允许时）
        # 这里默认不填充，只提交你这次阅读涉及的楼层

        js = """
        return (async () => {
          try {
            const url = arguments[0];
            const body = arguments[1];
            const csrf = arguments[2];
            const ref = arguments[3];

            const resp = await fetch(url, {
              method: "POST",
              mode: "cors",
              credentials: "include",
              referrer: ref,
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
            });

            let head = "";
            try { head = (await resp.text()).slice(0, 160); } catch(e) {}
            return {ok: resp.ok, status: resp.status, head: head};
          } catch (e) {
            return {ok: false, status: -1, head: String(e).slice(0,160)};
          }
        })();
        """

        result = None
        try:
            result = page.run_js(js, TIMINGS_URL, body, csrf, ref_url)
        except Exception as e:
            result = {"ok": False, "status": -2, "head": str(e)[:160]}

        self.timings_sent += 1
        ok = bool(result and result.get("ok"))
        status = result.get("status") if result else None
        head = (result.get("head") if result else "") or ""
        if ok:
            self.timings_ok += 1
        else:
            self.timings_fail += 1

        logger.info(
            f"timings(fetch): status={status} ok={1 if ok else 0} "
            f"topic_id={topic_id} posts={post_ids} topic_time={topic_time} body={body}"
        )
        if not ok and head:
            logger.info(f"timings(fetch): head={head}")

        # 模仿扩展：请求之间加一点 delay
        delay_ms = TIMINGS_BASE_DELAY_MS + random.randint(0, TIMINGS_RANDOM_DELAY_MS)
        time.sleep(delay_ms / 1000.0)

        logger.info(f"timings: totals sent={self.timings_sent} ok={self.timings_ok} fail={self.timings_fail}")
        return ok

    # ----------------------------
    # Human-like reading (按你“可用脚本”的滚动节奏)
    # ----------------------------
    def _read_like_human_with_scroll(self, page, seconds: float):
        """
        ✅ 完全按你那份“能用脚本”的节奏：
        - 每次 scrollBy 200~500
        - 间隔 1~3 秒
        - 期间触发 scroll / focus / mousemove（加一点）
        """
        end = time.time() + seconds
        while time.time() < end:
            dist = random.randint(READ_SCROLL_MIN, READ_SCROLL_MAX)
            try:
                page.run_js(
                    """
                    try { window.focus(); } catch(e) {}
                    try {
                      const ev = new MouseEvent('mousemove', {
                        clientX: 80 + Math.random()*600,
                        clientY: 80 + Math.random()*400
                      });
                      document.dispatchEvent(ev);
                    } catch(e) {}
                    try {
                      window.scrollBy(0, arguments[0]);
                      window.dispatchEvent(new Event('scroll'));
                    } catch(e) {}
                    """,
                    dist,
                )
            except Exception:
                pass
            time.sleep(random.uniform(READ_INTERVAL_MIN, READ_INTERVAL_MAX))

    def _read_post_like_human(self, page, post_id: int):
        """
        ✅ 只读“仍有蓝点”的楼层：
        - 滚到楼层中间
        - 按真人节奏滚动阅读 >= MIN_READ_STAY
        - 阅读后：用扩展同款 fetch 提交 timings（核心）
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

        stay = max(MIN_READ_STAY, random.uniform(MIN_READ_STAY, MIN_READ_STAY + 6.0))
        logger.info(f"👀 阅读未读楼层 post_{post_id}（阅读滚动≈{stay:.1f}s）")

        # 阅读滚动（按你那份“可用脚本”）
        self._read_like_human_with_scroll(page, stay)

        # ✅ 阅读后：从视口收集一批楼层去打 timings（更像你抓包：一次 3~6 个楼层）
        vp = self._list_visible_posts_in_viewport(page)
        if ONLY_TIMINGS_UNREAD:
            vp = [pid for pid in vp if self._post_has_blue_dot(page, pid)]
        if not vp:
            # fallback：至少把当前 post_id 打进去
            vp = [post_id]

        # 控制 batch size，模仿扩展（minReq~maxReq）
        want = random.randint(TIMINGS_MIN_REQ, TIMINGS_MAX_REQ)
        batch = vp[:want]

        ok = self._post_timings_via_page_fetch(page, batch)
        if ok:
            return True

        # 如果失败，不再死等 UI；仅提示（因为 UI 不一定同步）
        logger.warning(
            f"⚠️ post_{post_id} 本次 timings 未成功（status!=200）；UI 未见 read-state.read 不代表一定没计阅读"
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
    # Browse replies (5-10 pages) + 只读蓝点楼层 + timings
    # ----------------------------
    def browse_replies_pages(self, page, min_pages=5, max_pages=10):
        if max_pages < min_pages:
            max_pages = min_pages
        target_pages = random.randint(min_pages, max_pages)
        logger.info(f"目标：浏览评论 {target_pages} 页（按楼层号增长计，PAGE_GROW={PAGE_GROW}）")

        self.wait_topic_posts_ready(page, timeout=60)

        pages_done = 0
        last_max_no = self._max_post_number_in_dom(page)
        last_cnt = self._post_count_in_dom(page)
        logger.info(f"初始：max_post_no={last_max_no}, dom_posts={last_cnt}")

        max_loops = int(target_pages * 10 + 25)
        seen_read_attempts = set()

        for i in range(max_loops):
            # 1) 大步滚动推进
            scroll_distance = random.randint(SCROLL_MIN, SCROLL_MAX)
            logger.info(f"[loop {i+1}] 向下滚动 {scroll_distance}px 浏览评论...")
            try:
                page.run_js("window.scrollBy(0, arguments[0]);", scroll_distance)
            except Exception:
                pass

            # 2) 等待加载
            time.sleep(random.uniform(1.0, 1.8))

            # 3) 视口内：只读“仍有蓝点”的楼层（最多 1~3 个）
            vp = self._list_visible_posts_in_viewport(page)
            unread = [pid for pid in vp if self._post_has_blue_dot(page, pid)]
            unread = [pid for pid in unread if pid not in seen_read_attempts]

            if unread:
                k = min(len(unread), random.randint(1, 3))
                for pid in unread[:k]:
                    seen_read_attempts.add(pid)
                    self._read_post_like_human(page, pid)

            # 4) 页计数：max_post_no 增长
            cur_max_no = self._max_post_number_in_dom(page)
            cur_cnt = self._post_count_in_dom(page)

            if cur_max_no - last_max_no >= PAGE_GROW:
                pages_done += 1
                logger.success(
                    f"✅ 第 {pages_done}/{target_pages} 页：max_post_no {last_max_no} -> {cur_max_no}（dom_posts={cur_cnt}）"
                )
                last_max_no = cur_max_no
                last_cnt = cur_cnt

            # 5) near-bottom：额外阅读滚动，促发加载/计时
            if self._near_bottom(page, gap=NEAR_BOTTOM_GAP):
                extra = random.uniform(BOTTOM_EXTRA_STAY_MIN, BOTTOM_EXTRA_STAY_MAX)
                logger.info(f"[loop {i+1}] 接近底部（gap<={NEAR_BOTTOM_GAP}px），额外阅读≈{extra:.1f}s")
                self._read_like_human_with_scroll(page, extra)

            # 6) 达标退出
            if pages_done >= target_pages:
                logger.success("🎉 已达到目标评论页数，结束浏览")
                return True

            # 7) 到底判断
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
                f" + 浏览任务完成(话题<= {MAX_TOPICS} 个, 评论{MIN_COMMENT_PAGES}-{MAX_COMMENT_PAGES}页, "
                f"PAGE_GROW={PAGE_GROW}, MIN_READ_STAY={MIN_READ_STAY}s, HEADLESS={HEADLESS}, DP_PORT={DP_PORT}, "
                f"timings_ok={self.timings_ok}/{self.timings_sent})"
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
