import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

import aiohttp
from bs4 import BeautifulSoup
from loguru import logger

MAX_WORKERS = 20
MAX_CONCURRENT_REQUESTS_PER_HOST = 7


@dataclass
class ScrapeTask:
    url: str
    parent_url: Optional[str] = None


class WebScraper:
    def __init__(self, start_url, match_full_domain=False):
        self.start_url = start_url
        self.match_full_domain = match_full_domain
        self.source_domain = self.get_domain(start_url)
        logger.info(f"Domain scope is {self.source_domain}")
        self.visited_urls = set()
        self.broken_links = {}  # {page_url: [broken_links]}
        self.timeout_links = {}  # {page_url: [timeout_links]}
        self.queue = asyncio.Queue()

    def get_domain(self, url):
        netloc = urlparse(url).netloc
        if self.match_full_domain:
            return netloc
        else:
            parts = netloc.split(".")
            return ".".join(parts[-2:])

    @staticmethod
    def get_base_domain(url):
        netloc = urlparse(url).netloc
        parts = netloc.split(".")
        return ".".join(parts[-2:])

    @staticmethod
    def dedupe_url(url):
        parsed = urlparse(url)
        return urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, "")
        )

    async def fetch_and_check_url(self, session, request_url, parent_url=None):
        """Fetch a URL and check its status. Returns (content, resolved_url) tuple if it's a page we should scrape."""
        source_page = parent_url if parent_url is not None else request_url

        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with session.get(request_url, timeout=timeout) as response:
                resolved_url = str(response.url)  # Get final URL after redirects

                # Check if it's a 404 first
                if response.status == 404:
                    logger.warning(f"404 error at {request_url} (from {source_page})")
                    if source_page not in self.broken_links:
                        self.broken_links[source_page] = []
                    self.broken_links[source_page].append(request_url)
                    return None, None

                # Log redirect if it occurred
                if resolved_url != request_url:
                    logger.debug(f"Redirect: {request_url} -> {resolved_url}")

                # Check content type
                content_type = response.headers.get("Content-Type", "").lower()
                is_html = "text/html" in content_type

                # If it's not HTML, we just verify it works but don't return content
                if not is_html:
                    logger.debug(
                        f"Skipping non-HTML content at {request_url} (type: {content_type})"
                    )
                    return None, None

                # Only return content for HTML pages we need to scrape (same domain as original request)
                if self.get_domain(request_url) == self.source_domain:
                    try:
                        content = await response.text()
                        return content, resolved_url
                    except Exception as e:
                        logger.error(f"Error fetching {request_url}: {str(e)}")
                return None, None

        except asyncio.TimeoutError:
            if source_page not in self.timeout_links:
                self.timeout_links[source_page] = []
            self.timeout_links[source_page].append(request_url)
            logger.error(f"Timeout accessing {request_url}")
            return None, None
        except aiohttp.ClientError as e:
            if source_page not in self.broken_links:
                self.broken_links[source_page] = []
            self.broken_links[source_page].append(request_url)
            logger.error(f"Error accessing {request_url}: {str(e)}")
            return None, None

    def extract_links(self, html, base_url):
        """Extract all links from the HTML content."""
        soup = BeautifulSoup(html, "html.parser")
        links = []

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if href.startswith("mailto:"):
                continue
            absolute_url = urljoin(base_url, href)
            links.append(absolute_url)

        return links

    async def worker(self, session, worker_id):
        """Worker that processes URLs from the queue."""
        logger.info(f"Worker {worker_id} started")

        while True:
            try:
                task = await self.queue.get()
                url = task.url
                parent_url = task.parent_url

                logger.info(f"Processing: {url} (from {parent_url})")

                html, resolved_url = await self.fetch_and_check_url(
                    session, url, parent_url
                )
                if html is None:
                    self.queue.task_done()
                    continue

                links = self.extract_links(html, resolved_url)

                for link in links:
                    deduped_link = WebScraper.dedupe_url(link)
                    if deduped_link in self.visited_urls:
                        continue
                    await self.queue.put(
                        ScrapeTask(link, url)
                    )  # Track original URL, not the resolved URL
                    self.visited_urls.add(deduped_link)

                self.queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {str(e)}")
                self.queue.task_done()

    async def run(self):
        """Run the scraper with multiple workers."""
        try:
            connector = aiohttp.TCPConnector(
                limit_per_host=MAX_CONCURRENT_REQUESTS_PER_HOST
            )  # Limit concurrent connections
            async with aiohttp.ClientSession(connector=connector) as session:
                # Start with the initial URL
                await self.queue.put(ScrapeTask(self.start_url))

                # Create workers
                workers = [
                    asyncio.create_task(self.worker(session, i))
                    for i in range(MAX_WORKERS)
                ]

                try:
                    # Wait for the queue to be empty
                    await self.queue.join()
                except (KeyboardInterrupt, asyncio.CancelledError):
                    logger.info("Received interrupt, stopping workers...")
                finally:
                    # Cancel workers
                    for w in workers:
                        w.cancel()

                    # Wait for workers to finish
                    await asyncio.gather(*workers, return_exceptions=True)
        finally:
            # Print results no matter what happened
            if self.broken_links or self.timeout_links:
                print("\n\033[1;31mBroken Links Report:\033[0m")
                print("\033[1;31m===================\033[0m")
                for page, broken_links in self.broken_links.items():
                    print(f"\n\033[1;37mOn page: {page}\033[0m")
                    print("\033[1;31m404 Errors:\033[0m")
                    for link in broken_links:
                        print(f"\033[0;31m  - {link}\033[0m")

                print("\n\033[1;33mTimeout Links Report:\033[0m")
                print("\033[1;33m====================\033[0m")
                for page, timeout_links in self.timeout_links.items():
                    print(f"\n\033[1;37mOn page: {page}\033[0m")
                    print("\033[1;33mTimeout Errors:\033[0m")
                    for link in timeout_links:
                        print(f"\033[0;33m  - {link}\033[0m")
            else:
                print("\n\033[1;32mNo broken or timeout links found!\033[0m")


def main():
    parser = argparse.ArgumentParser(description="Scan a website for broken links")
    parser.add_argument(
        "url",
        help="URL to scan for broken links (will stick to this URL's base domain)",
    )
    parser.add_argument(
        "--match-full-domain",
        action="store_true",
        help="Match the full domain instead of just the base domain (e.g., subdomain.example.com vs example.com)",
    )
    args = parser.parse_args()
    logger.configure(handlers=[{"sink": sys.stdout, "level": "DEBUG"}])

    try:
        url = args.url
        result = urlparse(url)
        if not all([result.scheme, result.netloc]):
            if not url.startswith(("http://", "https://")):
                url = "http://" + url
            result = urlparse(url)
            if not all([result.scheme, result.netloc]):
                raise ValueError("Invalid URL format")
    except Exception:
        print("Please enter a valid URL")
        sys.exit(1)
    scraper = WebScraper(url, match_full_domain=args.match_full_domain)
    asyncio.run(scraper.run())


if __name__ == "__main__":
    main()
