"""
cron: 0 */6 * * *
new Env("Linux.Do 签到")
"""

# -*- coding: utf-8 -*-
import os
import random
import time
import functools
import re
import tempfile
import shutil
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
                        break
                    logger.warning(
                        f"函数 {func.__name__} 第 {attempt + 1}/{retries} 次尝试失败: {str(e)}"
                    )
                    sleep_s = random.uniform(min_delay, max_delay)
                    logger.info(f"将在 {sleep_s:.2f}s 后重试")
                    time.sleep(sleep_s)
            if last_err:
                raise last_err

        return wrapper

    return decorator


# ----------------------------
# Env & Config
# ----------------------------
os.environ.pop("DYLD_LIBRARY_PATH", None)

USERNAME = os.environ.get("LINUXDO_USERNAME") or os.environ.get("USERNAME")
PASSWORD = os.environ.get("LINUXDO_PASSWORD") or os.environ.get("PASSWORD")

BROWSE_ENABLED = os.environ.get("BROWSE_ENABLED", "true").strip().lower() not in [
    "false",
    "0",
    "off",
]

# ✅ 默认：Actions 更稳。若你坚持非 headless + Xvfb，就在 workflow 里 HEADLESS=false
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() not in ["false", "0", "off"]

MAX_TOPICS = int(os.environ.get("MAX_TOPICS", "50"))
MIN_COMMENT_PAGES = int(os.environ.get("MIN_COMMENT_PAGES", "5"))
MAX_COMMENT_PAGES = int(os.environ.get("MAX_COMMENT_PAGES", "10"))
PAGE_GROW = int(os.environ.get("PAGE_GROW", "10"))
LIKE_PROB = float(os.environ.get("LIKE_PROB", "0.3"))

# ✅ 推进式“大步滚动”
SCROLL_MIN = int(os.environ.get("SCROLL_MIN", "1000"))
SCROLL_MAX = int(os.environ.get("SCROLL_MAX", "1600"))

# ✅ 阅读式滚动（借鉴你可用脚本：200~500 + 1~3s）
READ_SCROLL_MIN = int(os.environ.get("READ_SCROLL_MIN", "200"))
READ_SCROLL_MAX = int(os.environ.get("READ_SCROLL_MAX", "500"))
READ_SCROLL_WAIT_MIN = float(os.environ.get("READ_SCROLL_WAIT_MIN", "1"))
READ_SCROLL_WAIT_MAX = float(os.environ.get("READ_SCROLL_WAIT_MAX", "3"))

# ✅ 固定默认（你要求写死），也允许 env 覆盖
MIN_READ_STAY = float(os.environ.get("MIN_READ_STAY", "5"))
READ_STATE_TIMEOUT = float(os.environ.get("READ_STATE_TIMEOUT", "20"))

MAX_LOOP_FACTOR = float(os.environ.get("MAX_LOOP_FACTOR", "10"))
NEAR_BOTTOM_GAP = int(os.environ.get("NEAR_BOTTOM_GAP", "140"))
BOTTOM_EXTRA_STAY_MIN = float(os.environ.get("BOTTOM_EXTRA_STAY_MIN", "6"))
BOTTOM_EXTRA_STAY_MAX = float(os.environ.get("BOTTOM_EXTRA_STAY_MAX", "12"))

# ✅ timings 只记录（前端自己发的）——不主动发
TIMINGS_LOG_ENABLED = os.environ.get("TIMINGS_LOG_ENABLED", "true").strip().lower() not in [
    "false",
    "0",
    "off",
]

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

        # ✅ 独立 profile，减少 9222 连接冲突
        self._user_data_dir = tempfile.mkdtemp(prefix="linuxdo_dp_profile_")

        co = ChromiumOptions().incognito(True)
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument(f"--user-data-dir={self._user_data_dir}")
        co.set_argument("--remote-debugging-port=9222")

        # 减少后台节流（帮助前端自动上报 timings）
        co.set_argument("--disable-background-timer-throttling")
        co.set_argument("--disable-backgrounding-occluded-windows")
        co.set_argument("--disable-renderer-backgrounding")

        co.headless(HEADLESS)

        co.set_user_agent(
            f"Mozilla/5.0 ({platformIdentifier}) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/130.0.0.0 Safari/537.36"
        )

        self.browser = Chromium(co)
        self.page = self.browser.new_tab()

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )

        # timings 统计（仅“观察到”的）
        self.timings_seen = 0
        self.timings_seen_ok = 0
        self.timings_seen_fail = 0

        self._timings_hook_installed = False

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
            js = 'return document.querySelectorAll("[id^=\\"post_\\"]").length;'
            return int(page.run_js(js) or 0)
        except Exception:
            return 0

    def _max_post_number_in_dom(self, page) -> int:
        try:
            return int(
                page.run_js(
                    r"""
                    let maxN = 0;
                    document.querySelectorAll('[id^="post_"]').forEach(el => {
                      const m = el.id.match(/^post_(\d+)$/);
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
        ✅ 不依赖 #post_1：
        只要存在任意 post_数字 且正文区域有文本 => ready
        """
        end = time.time() + timeout
        while time.time() < end:
            try:
                ok = page.run_js(
                    """
                    const posts = Array.from(document.querySelectorAll('[id^="post_"]'));
                    if (!posts.length) return false;
                    for (const p of posts) {
                      const c = p.querySelector(arguments[0]);
                      if (!c) continue;
                      const t = (c.innerText || c.textContent || '').trim();
                      if (t.length > 0) return true;
                    }
                    return false;
                    """,
                    POST_CONTENT_CSS,
                )
                if ok:
                    cnt = self._post_count_in_dom(page)
                    mx = self._max_post_number_in_dom(page)
                    mn = int(
                        page.run_js(
                            r"""
                            let minN = 999999;
                            document.querySelectorAll('[id^="post_"]').forEach(el=>{
                              const m = el.id.match(/^post_(\d+)$/);
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
    # read-state helpers
    # ----------------------------
    def _post_has_blue_dot(self, page, post_id: int) -> bool:
        """
        蓝点判断：存在 .read-state 且不包含 class 'read' => 未读
        """
        try:
            return bool(
                page.run_js(
                    r"""
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

    def _post_is_read(self, page, post_id: int) -> bool:
        try:
            return bool(
                page.run_js(
                    r"""
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
                r"""
                const els = Array.from(document.querySelectorAll('[id^="post_"]'));
                const v = [];
                for (const el of els) {
                  const r = el.getBoundingClientRect();
                  if (r.bottom < 0 || r.top > window.innerHeight) continue;
                  const m = el.id.match(/^post_(\d+)$/);
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
    # timings hook (observe only, never send)
    # ----------------------------
    def _install_timings_hook_if_needed(self, page):
        if not TIMINGS_LOG_ENABLED:
            return
        if self._timings_hook_installed:
            return
        try:
            ok = page.run_js(
                r"""
                (function(){
                  try {
                    if (window.__ld_timings_hooked__) return true;
                    window.__ld_timings_hooked__ = true;
                    window.__ld_timings_log__ = window.__ld_timings_log__ || [];

                    const pushLog = (obj) => {
                      try {
                        obj.t = Date.now();
                        window.__ld_timings_log__.push(obj);
                        if (window.__ld_timings_log__.length > 200) {
                          window.__ld_timings_log__.splice(0, 100);
                        }
                      } catch(e){}
                    };

                    // hook fetch
                    const origFetch = window.fetch;
                    if (origFetch) {
                      window.fetch = async function(input, init){
                        let url = '';
                        try {
                          url = (typeof input === 'string') ? input : (input && input.url) ? input.url : '';
                        } catch(e){}
                        let body = '';
                        try { body = (init && init.body) ? String(init.body) : ''; } catch(e){}
                        const isTimings = (url || '').includes('/topics/timings');
                        const ref = location.href;

                        let resp;
                        try {
                          resp = await origFetch.apply(this, arguments);
                        } catch (e) {
                          if (isTimings) pushLog({via:'fetch', url, status:-1, ref, body, err:String(e).slice(0,160)});
                          throw e;
                        }
                        if (isTimings) {
                          pushLog({via:'fetch', url, status:resp.status, ref, body});
                        }
                        return resp;
                      };
                    }

                    // hook XHR
                    const XHR = window.XMLHttpRequest;
                    if (XHR) {
                      const open0 = XHR.prototype.open;
                      const send0 = XHR.prototype.send;
                      XHR.prototype.open = function(method, url){
                        try {
                          this.__ld_url__ = url ? String(url) : '';
                          this.__ld_method__ = method ? String(method) : '';
                        } catch(e){}
                        return open0.apply(this, arguments);
                      };
                      XHR.prototype.send = function(body){
                        try { this.__ld_body__ = body ? String(body) : ''; } catch(e){}
                        const url = this.__ld_url__ || '';
                        const isTimings = url.includes('/topics/timings');
                        const ref = location.href;
                        if (isTimings) {
                          const xhr = this;
                          const done = function(){
                            try {
                              pushLog({via:'xhr', url, status:xhr.status, ref, body:xhr.__ld_body__ || ''});
                            } catch(e){}
                            try { xhr.removeEventListener('loadend', done); } catch(e){}
                          };
                          try { this.addEventListener('loadend', done); } catch(e){}
                        }
                        return send0.apply(this, arguments);
                      };
                    }

                    return true;
                  } catch(e) {
                    return false;
                  }
                })();
                """
            )
            self._timings_hook_installed = bool(ok)
            if ok:
                logger.info("timings hook 已注入（仅观察，不主动发送）")
            else:
                logger.warning("timings hook 注入失败（可能被 CSP/页面异常）")
        except Exception:
            logger.warning("timings hook 注入异常（跳过）")

    def _drain_timings_log(self, page):
        if not TIMINGS_LOG_ENABLED:
            return
        if not self._timings_hook_installed:
            return
        try:
            logs = page.run_js(
                r"""
                try {
                  const arr = window.__ld_timings_log__ || [];
                  const out = arr.slice(0);
                  window.__ld_timings_log__ = [];
                  return out;
                } catch(e) {
                  return [];
                }
                """
            )
            if not logs:
                return

            for item in logs:
                via = item.get("via")
                url = item.get("url")
                status = item.get("status")
                ref = item.get("ref")
                body = item.get("body", "")
                body = (body or "")[:260]

                self.timings_seen += 1
                if isinstance(status, int) and 200 <= status < 300:
                    self.timings_seen_ok += 1
                else:
                    self.timings_seen_fail += 1

                logger.info(
                    f"timings(observed): via={via} status={status} url={url} ref={ref} body={body}"
                )

            logger.info(
                f"timings(observed): totals seen={self.timings_seen} ok={self.timings_seen_ok} fail={self.timings_seen_fail}"
            )
        except Exception:
            return

    # ----------------------------
    # read style (200~500 + 1~3s)
    # ----------------------------
    def _scroll_read_style(self, page, duration_s: float):
        start = time.time()
        loops = 0
        while time.time() - start < duration_s:
            dist = random.randint(READ_SCROLL_MIN, READ_SCROLL_MAX)
            wait_s = random.uniform(READ_SCROLL_WAIT_MIN, READ_SCROLL_WAIT_MAX)
            try:
                page.run_js(
                    r"""
                    try { window.focus(); } catch(e) {}
                    try {
                      const ev = new MouseEvent('mousemove', {
                        clientX: 80 + Math.random()*500,
                        clientY: 60 + Math.random()*400
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

            time.sleep(wait_s)
            loops += 1

            # 每轮读完都尝试把“前端自己发的 timings”吐出来（只记录）
            self._drain_timings_log(page)

            try:
                at_bottom = page.run_js(
                    r"return (window.innerHeight + window.scrollY) >= (document.body.offsetHeight - 100);"
                )
            except Exception:
                at_bottom = False
            if at_bottom:
                break

        return loops

    def _read_post_like_human(self, page, post_id: int):
        """
        只读未读（蓝点）楼层：
        - 滚到楼层中间
        - 阅读滚动 >= MIN_READ_STAY
        - 不强依赖 UI read-state.read（只做提示）
        - timings 只记录前端自然请求
        """
        # 确保 hook
        self._install_timings_hook_if_needed(page)

        try:
            page.run_js(
                r"""
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

        self._scroll_read_style(page, stay)

        # 给 UI 一点同步窗口（但不把它当“必须”）
        end = time.time() + READ_STATE_TIMEOUT
        while time.time() < end:
            # 读过程中前端可能发 timings，持续吐日志
            self._drain_timings_log(page)

            if self._post_is_read(page, post_id):
                return True
            time.sleep(0.6)

        logger.warning(
            f"⚠️ post_{post_id} 停留已达阈值但 UI 未见 read-state.read（不一定代表未计阅读）"
        )
        return False

    # ----------------------------
    # Near-bottom
    # ----------------------------
    def _near_bottom(self, page, gap=140) -> bool:
        try:
            return bool(
                page.run_js(
                    r"""
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
    # Browse replies
    # ----------------------------
    def browse_replies_pages(self, page, min_pages=5, max_pages=10):
        if max_pages < min_pages:
            max_pages = min_pages
        target_pages = random.randint(min_pages, max_pages)
        logger.info(f"目标：浏览评论 {target_pages} 页（按楼层号增长计，PAGE_GROW={PAGE_GROW}）")

        self.wait_topic_posts_ready(page, timeout=60)
        self._install_timings_hook_if_needed(page)

        pages_done = 0
        last_max_no = self._max_post_number_in_dom(page)
        last_cnt = self._post_count_in_dom(page)
        logger.info(f"初始：max_post_no={last_max_no}, dom_posts={last_cnt}")

        max_loops = int(target_pages * MAX_LOOP_FACTOR + 20)
        seen_read_attempts = set()

        for i in range(max_loops):
            scroll_distance = random.randint(SCROLL_MIN, SCROLL_MAX)
            logger.info(f"[loop {i+1}] 向下滚动 {scroll_distance}px 浏览评论...")
            try:
                page.run_js("window.scrollBy(0, arguments[0]);", scroll_distance)
            except Exception:
                pass

            time.sleep(random.uniform(1.2, 2.0))
            self._drain_timings_log(page)

            vp = self._list_visible_posts_in_viewport(page)
            unread = [pid for pid in vp if self._post_has_blue_dot(page, pid)]
            unread = [pid for pid in unread if pid not in seen_read_attempts]

            if unread:
                k = min(len(unread), random.randint(1, 3))
                for pid in unread[:k]:
                    seen_read_attempts.add(pid)
                    self._read_post_like_human(page, pid)

            cur_max_no = self._max_post_number_in_dom(page)
            cur_cnt = self._post_count_in_dom(page)

            if cur_max_no - last_max_no >= PAGE_GROW:
                pages_done += 1
                logger.success(
                    f"✅ 第 {pages_done}/{target_pages} 页：max_post_no {last_max_no} -> {cur_max_no}（dom_posts={cur_cnt}）"
                )
                last_max_no = cur_max_no
                last_cnt = cur_cnt

            if self._near_bottom(page, gap=NEAR_BOTTOM_GAP):
                extra = random.uniform(BOTTOM_EXTRA_STAY_MIN, BOTTOM_EXTRA_STAY_MAX)
                logger.info(
                    f"[loop {i+1}] 接近底部（gap<={NEAR_BOTTOM_GAP}px），额外阅读≈{extra:.1f}s"
                )
                self._scroll_read_style(page, extra)

            if pages_done >= target_pages:
                logger.success("🎉 已达到目标评论页数，结束浏览")
                return True

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
            self._install_timings_hook_if_needed(new_page)
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
        headers = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
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
                f"PAGE_GROW={PAGE_GROW}, MIN_READ_STAY={MIN_READ_STAY}s, HEADLESS={HEADLESS}, "
                f"timings(observed ok/fail)={self.timings_seen_ok}/{self.timings_seen_fail})"
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
                    headers={"Authorization": WXPUSH_TOKEN, "Content-Type": "application/json"},
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
            try:
                shutil.rmtree(self._user_data_dir, ignore_errors=True)
            except Exception:
                pass


if __name__ == "__main__":
    if not USERNAME or not PASSWORD:
        print("Please set LINUXDO_USERNAME/LINUXDO_PASSWORD (or USERNAME/PASSWORD)")
        raise SystemExit(1)

    l = LinuxDoBrowser()
    l.run()
