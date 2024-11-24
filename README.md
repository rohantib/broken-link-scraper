# Website 404 Scraper

A Python script that recursively crawls a website starting from a base URL, checking for broken links (404s) and timeouts across the entire domain.

This will check the entire base domain, e.g. given "en.example.com" the script will follow all links with domain "example.com".

## Disclaimer

This tool was developed as an experimental project to explore working with [Cursor](https://cursor.com/) and managing Python dependencies with [uv](https://github.com/astral-sh/uv). While effective at identifying many broken links, the script may occasionally produce false positives or miss certain broken links due to its experimental nature. It serves as a useful supplementary tool for link checking rather than a comprehensive production-grade solution.

(This README is also mostly Claude 3.5 Sonnet generated.)

## Overview

This tool helps identify broken links and connectivity issues by:

- Starting from a provided base URL and crawling all pages within the same parent domain
- Checking both internal and external links for 404 errors and timeouts
- Using asynchronous I/O for efficient concurrent processing
- Generating a detailed report of all broken/timeout links grouped by the pages they appear on

## Features

- Asynchronous crawling with configurable number of workers
- Smart handling of relative and absolute URLs
- Timeout detection for slow/unresponsive links
- Domain-scoped crawling (only crawls pages within the base domain)
- Detailed console output with logging
- HTML content type detection

## Requirements

- Dependencies managed via `uv` (see `pyproject.toml`)
  - aiohttp
  - beautifulsoup4
  - loguru

## Usage

- With `uv`:
  - `uv run scraper.py`
- Without `uv`:
  - Install the listed dependencies then run `scraper.py`
- Provide a url when the script requests it