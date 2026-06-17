"""
md_crawler.py — 网站内容爬虫，输出 Markdown 文件
将公司网站页面提取为干净的 Markdown，用于知识库语料。

依赖:
    pip install requests beautifulsoup4 lxml markdownify

使用方法:
    python md_crawler.py --url https://www.example.com
    python md_crawler.py --url https://www.example.com --output ./output --max-pages 100
"""

import re
import time
import logging
import argparse
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from collections import deque

import requests
from bs4 import BeautifulSoup, Comment
from markdownify import markdownify as md

# ── 日志 ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── 噪声选择器（爬取前直接从 DOM 删除） ─────────────────────────────
NOISE_SELECTORS = [
    "nav", "header", "footer", "aside",
    ".nav", ".navbar", ".header", ".footer", ".sidebar", ".side-bar",
    ".menu", ".breadcrumb", ".pagination", ".pager",
    ".cookie", ".cookie-banner", ".gdpr",
    ".popup", ".modal", ".overlay", ".dialog",
    ".ad", ".ads", ".advertisement", ".adsbygoogle",
    ".social", ".share", ".share-bar", ".follow",
    ".comment", ".comments", ".disqus",
    ".related", ".recommend", ".also-read",
    ".tag-cloud", ".widget", ".newsletter",
    "script", "style", "noscript", "iframe", "form",
    '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
    '[aria-hidden="true"]',
]

# ── 正文选择器（按优先级匹配第一个） ────────────────────────────────
CONTENT_SELECTORS = [
    "article",
    "main",
    '[role="main"]',
    ".article", ".article-body", ".article-content",
    ".post", ".post-body", ".post-content",
    ".content", ".page-content", ".main-content", ".entry-content",
    ".product-detail", ".product-description", ".product-info",
    ".docs-content", ".doc-body",
    ".help-content", ".support-content",
    "#content", "#main", "#article",
]

# ── 跳过这些扩展名 ───────────────────────────────────────────────────
SKIP_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".tar", ".gz", ".7z",
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp",
    ".mp4", ".mp3", ".avi", ".mov", ".wmv", ".flv",
    ".css", ".js", ".json", ".xml", ".woff", ".woff2", ".ttf", ".eot",
}

# ── 跳过这些 URL 模式 ────────────────────────────────────────────────
SKIP_URL_RE = re.compile(
    r"(/login|/register|/signup|/cart|/checkout|/order"
    r"|/account|/profile|/admin|/dashboard"
    r"|/api/|/cdn-cgi/|/static/|/assets/"
    r"|/tag/|/category/|/author/"
    r"|[?&](page|p|offset)=\d+"   # 分页参数
    r")",
    re.IGNORECASE,
)


class MarkdownCrawler:
    """
    广度优先爬虫：
    1. 抓取同域页面
    2. 提取有效正文（过滤导航/广告/页脚等噪声）
    3. 将 HTML 转为干净的 Markdown
    4. 每个页面保存为独立 .md 文件
    """

    def __init__(
        self,
        start_url: str,
        output_dir: str = "./output",
        max_pages: int = 200,
        max_depth: int = 5,
        delay: float = 1.0,
        min_chars: int = 150,
    ):
        self.start_url  = start_url.rstrip("/")
        self.output_dir = Path(output_dir)
        self.max_pages  = max_pages
        self.max_depth  = max_depth
        self.delay      = delay
        self.min_chars  = min_chars

        parsed = urlparse(self.start_url)
        self.domain = parsed.netloc
        self.scheme = parsed.scheme

        self.visited: set   = set()
        self.seen_content: set = set()   # MD5 去重（跳过重复内容）
        self.queue: deque   = deque()
        self.stats          = {"fetched": 0, "saved": 0, "skipped": 0, "errors": 0}

        self.session        = self._make_session()
        self.robot_parser   = self._load_robots()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── 初始化 ──────────────────────────────────────────────────────

    def _make_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (compatible; KBCrawler/1.0)"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        return s

    def _load_robots(self) -> RobotFileParser:
        rp = RobotFileParser()
        robots_url = f"{self.scheme}://{self.domain}/robots.txt"
        try:
            rp.set_url(robots_url)
            rp.read()
            log.info(f"robots.txt 已加载: {robots_url}")
        except Exception:
            log.warning("无法读取 robots.txt，继续执行")
        return rp

    # ── URL 判断 ─────────────────────────────────────────────────────

    def _allowed_by_robots(self, url: str) -> bool:
        return self.robot_parser.can_fetch("*", url)

    def _same_domain(self, url: str) -> bool:
        return urlparse(url).netloc == self.domain

    def _should_skip(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return True
        ext = Path(parsed.path).suffix.lower()
        if ext in SKIP_EXTENSIONS:
            return True
        if SKIP_URL_RE.search(url):
            return True
        return False

    # ── 抓取 ─────────────────────────────────────────────────────────

    def _fetch(self, url: str) -> requests.Response | None:
        for attempt in range(1, 4):
            try:
                r = self.session.get(url, timeout=15, allow_redirects=True)
                r.raise_for_status()
                if "text/html" not in r.headers.get("Content-Type", ""):
                    return None
                return r
            except requests.RequestException as e:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                else:
                    log.error(f"请求失败: {url} — {e}")
                    self.stats["errors"] += 1
        return None

    # ── 内容提取 ─────────────────────────────────────────────────────

    def _remove_noise(self, soup: BeautifulSoup) -> None:
        """原地删除所有噪声节点"""
        # 删除 HTML 注释
        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()
        # 删除噪声选择器
        for sel in NOISE_SELECTORS:
            for el in soup.select(sel):
                el.decompose()

    def _find_content_node(self, soup: BeautifulSoup):
        """按优先级找到正文节点"""
        for sel in CONTENT_SELECTORS:
            node = soup.select_one(sel)
            if node:
                return node
        return soup.find("body")

    def _html_to_markdown(self, node) -> str:
        """将 BeautifulSoup 节点转为 Markdown"""
        html_str = str(node)
        result = md(
            html_str,
            heading_style="ATX",          # 使用 # 风格标题
            bullets="-",                   # 无序列表用 -
            strip=["script", "style"],
            convert_links=True,
            newline_style="backslash",
        )
        # 清理多余空行（超过 2 个连续空行 → 2 个）
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result.strip()

    def _is_content_useful(self, text: str) -> bool:
        """判断内容是否值得保存"""
        # 去掉 markdown 语法符号后计算纯文字长度
        plain = re.sub(r"[#*\-_\[\]`>|]", "", text)
        plain = re.sub(r"\s+", " ", plain).strip()
        return len(plain) >= self.min_chars

    # ── 文件名生成 ────────────────────────────────────────────────────

    def _url_to_filename(self, url: str) -> str:
        """将 URL 转为合法文件名"""
        parsed = urlparse(url)
        path = parsed.path.strip("/").replace("/", "_") or "index"
        # 去掉非法字符
        path = re.sub(r"[^\w\-]", "_", path)
        path = re.sub(r"_+", "_", path).strip("_")
        return (path[:80] or "index") + ".md"

    # ── 保存 ─────────────────────────────────────────────────────────

    def _save_markdown(self, url: str, title: str, content_md: str) -> Path:
        filename = self._url_to_filename(url)
        filepath = self.output_dir / filename

        # 文件名冲突时加序号
        if filepath.exists():
            stem = filepath.stem
            for i in range(2, 999):
                filepath = self.output_dir / f"{stem}_{i}.md"
                if not filepath.exists():
                    break

        # 写入：YAML front matter + 正文
        front_matter = (
            f"---\n"
            f"title: {title}\n"
            f"source: {url}\n"
            f"---\n\n"
        )
        filepath.write_text(front_matter + content_md, encoding="utf-8")
        return filepath

    # ── 链接提取 ──────────────────────────────────────────────────────

    def _extract_links(self, soup: BeautifulSoup, current_url: str) -> list[str]:
        links = []
        for tag in soup.find_all("a", href=True):
            href = tag["href"].strip()
            full = urljoin(current_url, href).split("#")[0].rstrip("/")
            if (
                full not in self.visited
                and self._same_domain(full)
                and not self._should_skip(full)
                and self._allowed_by_robots(full)
            ):
                links.append(full)
        return links

    # ── 主循环 ────────────────────────────────────────────────────────

    def run(self):
        log.info(f"开始爬取: {self.start_url}")
        log.info(f"输出目录: {self.output_dir.resolve()}")
        log.info(f"最大页面: {self.max_pages}，最大深度: {self.max_depth}")

        self.queue.append((self.start_url, 0))
        self.visited.add(self.start_url)

        while self.queue and self.stats["fetched"] < self.max_pages:
            url, depth = self.queue.popleft()

            if depth > self.max_depth:
                continue

            log.info(
                f"[{self.stats['fetched']+1}/{self.max_pages}] "
                f"深度 {depth} | {url}"
            )

            resp = self._fetch(url)
            self.stats["fetched"] += 1

            if resp is None:
                self.stats["skipped"] += 1
                continue

            soup = BeautifulSoup(resp.text, "lxml")

            # 提取标题（删噪声前）
            title = (soup.title.string or "").strip() if soup.title else ""

            # 提取子链接（删噪声前，否则 nav 链接会丢失）
            if depth < self.max_depth:
                new_links = self._extract_links(soup, url)
                for link in new_links:
                    if link not in self.visited:
                        self.visited.add(link)
                        self.queue.append((link, depth + 1))

            # 删除噪声 → 找正文 → 转 Markdown
            self._remove_noise(soup)
            content_node = self._find_content_node(soup)

            if content_node is None:
                self.stats["skipped"] += 1
                continue

            content_md = self._html_to_markdown(content_node)

            if not self._is_content_useful(content_md):
                log.info(f"  ✗ 内容过少，跳过")
                self.stats["skipped"] += 1
                continue

            # 内容去重
            import hashlib
            content_hash = hashlib.md5(content_md.encode()).hexdigest()
            if content_hash in self.seen_content:
                log.info(f"  ✗ 内容重复，跳过")
                self.stats["skipped"] += 1
                continue
            self.seen_content.add(content_hash)

            # 保存
            saved_path = self._save_markdown(url, title, content_md)
            self.stats["saved"] += 1
            log.info(f"  ✓ 已保存 → {saved_path.name}  ({len(content_md)} 字符)")

            time.sleep(self.delay)

        self._print_summary()

    def _print_summary(self):
        log.info("=" * 55)
        log.info("爬取完成！")
        log.info(f"  抓取页面: {self.stats['fetched']}")
        log.info(f"  保存文件: {self.stats['saved']}")
        log.info(f"  跳过页面: {self.stats['skipped']}")
        log.info(f"  请求失败: {self.stats['errors']}")
        log.info(f"  输出目录: {self.output_dir.resolve()}")
        log.info("=" * 55)


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="网站内容爬虫 → Markdown 知识库语料",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python md_crawler.py --url https://www.example.com
  python md_crawler.py --url https://www.example.com --output ./docs --max-pages 300
  python md_crawler.py --url https://www.example.com --delay 2 --min-chars 200
        """,
    )
    parser.add_argument("--url",        required=True,       help="起始 URL")
    parser.add_argument("--output",     default=r"D:\nvz\kefu\multi_agent\backed\knowledge\data\crawler",  help="输出目录（默认 ./output）")
    parser.add_argument("--max-pages",  type=int, default=200, help="最大抓取页面数（默认 200）")
    parser.add_argument("--max-depth",  type=int, default=10
                        ,   help="最大爬取深度（默认 5）")
    parser.add_argument("--delay",      type=float, default=1.0, help="请求间隔秒数（默认 1.0）")
    parser.add_argument("--min-chars",  type=int, default=150,  help="最短正文字符数（默认 150）")

    args = parser.parse_args()

    crawler = MarkdownCrawler(
        start_url  = args.url,
        output_dir = args.output,
        max_pages  = args.max_pages,
        max_depth  = args.max_depth,
        delay      = args.delay,
        min_chars  = args.min_chars,
    )
    crawler.run()


if __name__ == "__main__":
    main()