import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

import aiohttp
from bs4 import BeautifulSoup
from loguru import logger


@dataclass
class ScrapeTask:
    url: str
    parent_url: Optional[str] = None


class WebScraper:
    def __init__(self, start_url, max_workers=10):
        self.start_url = start_url
        self.domain = WebScraper.get_base_domain(start_url)
        logger.info(f"Base domain is {self.domain}")
        self.visited_urls = set()
        self.broken_links = {}  # {page_url: [broken_links]}
        self.timeout_links = {}  # {page_url: [timeout_links]}
        self.queue = asyncio.Queue()
        self.max_workers = max_workers

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

    async def fetch_and_check_url(self, session, url, parent_url=None):
        """Fetch a URL and check its status. Returns content if it's a page we should scrape."""
        source_page = parent_url if parent_url is not None else url

        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with session.get(url, timeout=timeout) as response:
                # Check if it's a 404 first
                if response.status == 404:
                    logger.debug(f"404 error at {url} (from {source_page})")
                    if source_page not in self.broken_links:
                        self.broken_links[source_page] = []
                    self.broken_links[source_page].append(url)
                    return None

                # Check content type
                content_type = response.headers.get("Content-Type", "").lower()
                is_html = "text/html" in content_type

                # If it's not HTML, we just verify it works but don't return content
                if not is_html:
                    logger.debug(
                        f"Skipping non-HTML content at {url} (type: {content_type})"
                    )
                    return None

                # Only return content for HTML pages we need to scrape (same domain)
                if WebScraper.get_base_domain(url) == self.domain:
                    try:
                        return await response.text()
                    except Exception as e:
                        logger.error(f"Error fetching {url}: {str(e)}")
                return None

        except asyncio.TimeoutError:
            if source_page not in self.timeout_links:
                self.timeout_links[source_page] = []
            self.timeout_links[source_page].append(url)
            logger.error(f"Timeout accessing {url}")
            return None
        except aiohttp.ClientError as e:
            if source_page not in self.broken_links:
                self.broken_links[source_page] = []
            self.broken_links[source_page].append(url)
            logger.error(f"Error accessing {url}: {str(e)}")
            return None

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

                html = await self.fetch_and_check_url(session, url, parent_url)
                if html is None:
                    self.queue.task_done()
                    continue

                links = self.extract_links(html, url)

                for link in links:
                    deduped_link = WebScraper.dedupe_url(link)
                    if deduped_link in self.visited_urls:
                        continue
                    await self.queue.put(ScrapeTask(link, url))
                    self.visited_urls.add(deduped_link)

                self.queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {str(e)}")
                self.queue.task_done()

    async def run(self):
        """Run the scraper with multiple workers."""
        connector = aiohttp.TCPConnector(limit=10)  # Limit concurrent connections
        async with aiohttp.ClientSession(connector=connector) as session:
            # Start with the initial URL
            await self.queue.put(ScrapeTask(self.start_url))

            # Create workers
            workers = [
                asyncio.create_task(self.worker(session, i))
                for i in range(self.max_workers)
            ]

            # Wait for the queue to be empty
            await self.queue.join()

            # Cancel workers
            for w in workers:
                w.cancel()

            # Wait for workers to finish
            await asyncio.gather(*workers, return_exceptions=True)

        # Print results
        if self.broken_links or self.timeout_links:
            print("\nBroken Links Report:")
            print("===================")
            for page, broken_links in self.broken_links.items():
                print(f"\nOn page: {page}")
                print("404 Errors:")
                for link in broken_links:
                    print(f"  - {link}")

            print("\nTimeout Links Report:")
            print("====================")
            for page, timeout_links in self.timeout_links.items():
                print(f"\nOn page: {page}")
                print("Timeout Errors:")
                for link in timeout_links:
                    print(f"  - {link}")
        else:
            print("\nNo broken or timeout links found!")


def main():
    parser = argparse.ArgumentParser(description="Scan a website for broken links")
    parser.add_argument(
        "url",
        help="URL to scan for broken links (will stick to this URL's base domain)",
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
    scraper = WebScraper(url)
    asyncio.run(scraper.run())


if __name__ == "__main__":
    main()
