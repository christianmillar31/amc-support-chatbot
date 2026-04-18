#!/usr/bin/env python3
"""
Scrape structured public metadata from the AMC website.

Outputs JSON artifacts under `site_data/`:
  - amc_products.json
  - amc_downloads_index.json
  - amc_glossary.json
  - amc_reserved_discontinued.json

This scraper is intentionally metadata-first. It focuses on structured page
content and document references rather than bulk-downloading gated assets.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag


BASE_URL = "https://www.a-m-c.com"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"
PRODUCT_SELECTOR_URL = f"{BASE_URL}/products/servo-drives/"
DOWNLOADS_URL = f"{BASE_URL}/downloads/"
GLOSSARY_URL = f"{BASE_URL}/glossary/"
RESERVED_URL = f"{BASE_URL}/support/reserved-discontinued/"

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "site_data"

HEADERS = {
    "User-Agent": "amc-support-bot-scraper/1.0 (+local metadata refresh)",
}


@dataclass
class ProductRecord:
    sku: str
    url: str
    title: str
    summary: str
    breadcrumb: list[str]
    specifications: dict[str, str]
    attributes: dict[str, list[str]]
    downloads: dict[str, list[dict[str, Any]]]
    related_products: list[dict[str, str]]


def make_session(cookie_header: str = "") -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    if cookie_header:
        session.headers["Cookie"] = cookie_header
    return session


def fetch_soup(session: requests.Session, url: str, timeout: int = 30) -> BeautifulSoup:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") + "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def normalize_download_url(url: str) -> str:
    parsed = urlparse(urljoin(BASE_URL, url))
    token = parsed.path.rstrip("/").split("/")[-1]
    if parsed.netloc == "amc.loc" and parsed.path.startswith("/wp-json/url/") and token:
        return f"{BASE_URL}/d/?h={token}"
    if parsed.netloc == urlparse(BASE_URL).netloc and parsed.path.startswith("/wp-json/url/") and token:
        return f"{BASE_URL}/d/?h={token}"
    return urljoin(BASE_URL, url)


def infer_access(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path == "/d/" and parse_qs(parsed.query).get("h"):
        return "registration_required"
    return "public"


def make_download_item(
    *,
    label: str,
    url: str,
    description: str = "",
    group: str = "",
) -> dict[str, Any]:
    normalized_url = normalize_download_url(url)
    item: dict[str, Any] = {
        "label": label,
        "url": normalized_url,
        "description": clean_text(description.lstrip("|")),
        "access": infer_access(normalized_url),
    }
    if group:
        item["group"] = group
    return item


def parse_sitemap_urls(session: requests.Session, url: str) -> list[str]:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    tag_name = root.tag.rsplit("}", 1)[-1]

    if tag_name == "sitemapindex":
        urls: list[str] = []
        for loc in root.findall("sm:sitemap/sm:loc", ns):
            urls.extend(parse_sitemap_urls(session, clean_text(loc.text or "")))
        return urls

    if tag_name == "urlset":
        return [clean_text(loc.text or "") for loc in root.findall("sm:url/sm:loc", ns)]

    return []


def discover_product_urls(session: requests.Session) -> list[str]:
    urls = parse_sitemap_urls(session, SITEMAP_URL)
    product_urls = {
        normalize_url(url)
        for url in urls
        if "/product/" in url and url.startswith(BASE_URL)
    }

    selector = fetch_soup(session, PRODUCT_SELECTOR_URL)
    for anchor in selector.select('a[href*="/product/"]'):
        href = anchor.get("href")
        if href:
            product_urls.add(normalize_url(urljoin(BASE_URL, href)))

    return sorted(product_urls)


def get_section_list_items(node: Tag) -> list[str]:
    items: list[str] = []
    current = node.find_next_sibling()
    while current and current.name not in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        if current.name == "ul":
            items.extend(clean_text(li.get_text(" ", strip=True)) for li in current.find_all("li"))
        current = current.find_next_sibling()
    return [item for item in items if item]


def get_section_container(node: Tag) -> Tag:
    """Return the smallest useful container that holds a heading and its section content."""
    parent = node.parent if isinstance(node.parent, Tag) else node
    if isinstance(parent.parent, Tag):
        parent_text = clean_text(parent.get_text(" ", strip=True))
        grand_text = clean_text(parent.parent.get_text(" ", strip=True))
        if grand_text and len(grand_text) > len(parent_text):
            return parent.parent
    return parent


def parse_item_text(full_text: str, label: str) -> str:
    desc = full_text.replace(label, "", 1).strip(" -")
    return clean_text(desc)


def parse_download_blocks(container: Tag, stop_labels: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for block in container.select("div.download"):
        link = block.find("a", href=True)
        if not link:
            continue
        label = clean_text(link.get_text(" ", strip=True))
        if not label or label in stop_labels:
            continue
        description_node = block.select_one(".download-description")
        description = clean_text(description_node.get_text(" ", strip=True)) if description_node else ""

        group = ""
        sibling = block.find_previous_sibling()
        while sibling:
            if sibling.name in {"h4", "h5", "h6"}:
                candidate = clean_text(sibling.get_text(" ", strip=True))
                if candidate and candidate not in stop_labels:
                    group = candidate
                break
            sibling = sibling.find_previous_sibling()

        items.append(
            make_download_item(
                label=label,
                url=link["href"],
                description=description,
                group=group,
            )
        )
    return items


def parse_list_items(container: Tag) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for li in container.find_all("li"):
        link = li.find("a", href=True)
        if not link:
            continue
        label = clean_text(link.get_text(" ", strip=True))
        if not label:
            continue
        description_node = li.find("span")
        description = clean_text(description_node.get_text(" ", strip=True)) if description_node else parse_item_text(clean_text(li.get_text(" ", strip=True)), label)
        items.append(make_download_item(label=label, url=link["href"], description=description))
    return items


def parse_paragraph_links(container: Tag, stop_labels: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    paragraphs = [p for p in container.find_all("p") if clean_text(p.get_text(" ", strip=True))]
    i = 0
    while i < len(paragraphs):
        p = paragraphs[i]
        link = p.find("a", href=True)
        if not link:
            i += 1
            continue
        label = clean_text(link.get_text(" ", strip=True))
        if not label or label in stop_labels or label in seen:
            i += 1
            continue

        description = parse_item_text(clean_text(p.get_text(" ", strip=True)), label)
        if not description and i + 1 < len(paragraphs):
            next_text = clean_text(paragraphs[i + 1].get_text(" ", strip=True))
            if next_text and next_text not in stop_labels:
                description = next_text
                i += 1

        seen.add(label)
        items.append(make_download_item(label=label, url=link["href"], description=description))
        i += 1
    return items


def dedupe_downloads(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (str(item.get("label", "")), str(item.get("url", "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def parse_link_block(node: Tag, stop_labels: Optional[set[str]] = None) -> list[dict[str, Any]]:
    container = get_section_container(node)
    stop_labels = stop_labels or set()
    items = parse_download_blocks(container, stop_labels)
    if not items:
        items = parse_list_items(container)
    if not items:
        items = parse_paragraph_links(container, stop_labels)
    return dedupe_downloads(items)


def parse_specifications(soup: BeautifulSoup) -> dict[str, str]:
    specs: dict[str, str] = {}
    heading = soup.find(re.compile("^h[1-6]$"), string=re.compile(r"Specifications", re.I))
    if not heading:
        return specs

    current = heading.find_next_sibling()
    while current and clean_text(current.get_text(" ", strip=True)).lower() != "add to quote":
        text = clean_text(current.get_text(" ", strip=True))
        if text and current.name in {"p", "div"}:
            match = re.match(r"^(.*?)(Product Status:|Current Continuous \(A\)|Current Peak \(A\)|DC Supply Voltage \(VDC\)|AC Supply Voltage \(VAC\)|Network Communication|Functional Safety|Size \(mm\)|Weight \(g\)|Family|Form Factor)(.*)$", text)
            if ":" in text:
                left, right = text.split(":", 1)
                specs[clean_text(left)] = clean_text(right)
            elif match:
                # Handles compressed text blocks like "Family FlexPro"
                labels = [
                    "Form Factor",
                    "Family",
                    "Product Status",
                    "Current Continuous (A)",
                    "Current Peak (A)",
                    "DC Supply Voltage (VDC)",
                    "AC Supply Voltage (VAC)",
                    "Network Communication",
                    "Functional Safety",
                    "Size (mm)",
                    "Weight (g)",
                ]
                for label in labels:
                    if text.startswith(label):
                        specs[label] = clean_text(text[len(label):])
                        break
        current = current.find_next_sibling()
    return specs


def parse_product_page(session: requests.Session, url: str) -> ProductRecord:
    soup = fetch_soup(session, url)

    title = clean_text(soup.find("h1").get_text(" ", strip=True))
    breadcrumb: list[str] = []
    breadcrumb_nav = soup.select_one(".woocommerce-breadcrumb")
    if breadcrumb_nav:
        breadcrumb = [
            clean_text(node.get_text(" ", strip=True))
            for node in breadcrumb_nav.find_all(["a", "span"])
            if clean_text(node.get_text(" ", strip=True)) not in {"", ">"}
        ]
    summary = ""
    h1 = soup.find("h1")
    if h1:
        sibling = h1.find_next_sibling()
        while sibling and sibling.name != "img":
            text = clean_text(sibling.get_text(" ", strip=True))
            if text:
                summary = text
                break
            sibling = sibling.find_next_sibling()

    attributes: dict[str, list[str]] = {}
    for heading in soup.find_all(re.compile("^h[1-6]$")):
        label = clean_text(heading.get_text(" ", strip=True))
        if label in {
            "CONTROL/COMMAND",
            "PRIMARY FEEDBACK",
            "OPERATING MODE",
            "MOTOR TYPE",
            "AUXILIARY FEEDBACK",
            "FUNCTIONAL SAFETY",
            "DRIVE INTELLIGENCE",
            "ENVIRONMENT",
        }:
            attributes[label] = get_section_list_items(heading)

    downloads: dict[str, list[dict[str, str]]] = {}
    for heading in soup.find_all(re.compile("^h[1-6]$")):
        label = clean_text(heading.get_text(" ", strip=True))
        if label in {
            "Product Downloads",
            "Software Downloads",
            "Application Notes",
            "Instructional Videos",
            "Compliance and Approvals",
            "White Papers",
        }:
            downloads[label] = parse_link_block(heading)

    related_products: list[dict[str, str]] = []
    related_heading = soup.find(re.compile("^h[1-6]$"), string=re.compile(r"Related Products", re.I))
    if related_heading:
        for link in related_heading.find_all_next("a", href=True):
            href = link.get("href", "")
            if "/product/" not in href:
                continue
            sku = clean_text(link.get_text(" ", strip=True))
            if not sku:
                continue
            related_products.append({"sku": sku, "url": urljoin(BASE_URL, href)})

    sku = title
    return ProductRecord(
        sku=sku,
        url=url,
        title=title,
        summary=summary,
        breadcrumb=breadcrumb[:6],
        specifications=parse_specifications(soup),
        attributes=attributes,
        downloads=downloads,
        related_products=related_products,
    )


def parse_downloads_index(session: requests.Session) -> dict[str, list[dict[str, str]]]:
    soup = fetch_soup(session, DOWNLOADS_URL)
    data: dict[str, list[dict[str, str]]] = {}
    for heading in soup.find_all(re.compile("^h[1-6]$")):
        label = clean_text(heading.get_text(" ", strip=True))
        if label in {
            "Hardware Manuals",
            "Communication Manuals",
            "Software Downloads",
            "Application Notes",
            "Instructional Videos",
            "Product Notes",
            "Compliance and Approvals",
        }:
            data[label] = parse_link_block(heading, stop_labels={
                "Hardware Manuals",
                "Communication Manuals",
                "Software Downloads",
                "Application Notes",
                "Instructional Videos",
                "Product Notes",
                "Compliance and Approvals",
            })
    return data


def parse_reserved_page(session: requests.Session) -> dict[str, list[dict[str, str]]]:
    soup = fetch_soup(session, RESERVED_URL)
    data: dict[str, list[dict[str, str]]] = {}
    intro = soup.find("h1")
    result = {"intro": clean_text(intro.find_next("p").get_text(" ", strip=True)) if intro else ""}
    for heading in soup.find_all(re.compile("^h[1-6]$")):
        label = clean_text(heading.get_text(" ", strip=True))
        if label.endswith("Downloads"):
            data[label] = parse_link_block(heading, stop_labels={"Hardware Downloads", "Software Downloads"})
    result["sections"] = data
    return result


def parse_glossary(session: requests.Session) -> list[dict[str, str]]:
    soup = fetch_soup(session, GLOSSARY_URL)
    entries: list[dict[str, str]] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [clean_text(th.get_text(" ", strip=True)) for th in rows[0].find_all(["th", "td"])]
        if headers[:2] != ["Term", "Definition"]:
            continue
        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            term = clean_text(cells[0].get_text(" ", strip=True))
            definition = clean_text(cells[1].get_text(" ", strip=True))
            synonyms = clean_text(cells[2].get_text(" ", strip=True)) if len(cells) > 2 else ""
            related_terms = clean_text(cells[3].get_text(" ", strip=True)) if len(cells) > 3 else ""
            if term and definition:
                entries.append(
                    {
                        "term": term,
                        "definition": definition,
                        "synonyms": synonyms,
                        "related_terms": related_terms,
                    }
                )
    return entries


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def chunked(items: Iterable[str], size: int) -> Iterable[list[str]]:
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape AMC website metadata into local JSON artifacts.")
    parser.add_argument("--limit-products", type=int, default=0, help="Optional limit for product pages.")
    parser.add_argument("--sleep", type=float, default=0.25, help="Delay between product page requests.")
    parser.add_argument("--cookie-header", default=os.getenv("AMC_COOKIE", ""), help="Optional Cookie header for gated content.")
    args = parser.parse_args()

    session = make_session(cookie_header=args.cookie_header)

    product_urls = discover_product_urls(session)
    if args.limit_products > 0:
        product_urls = product_urls[:args.limit_products]

    products: list[dict] = []
    for url in product_urls:
        try:
            record = parse_product_page(session, url)
            products.append(asdict(record))
            print(f"[product] {record.sku}")
        except Exception as exc:
            print(f"[product ERROR] {url}: {exc}")
        time.sleep(args.sleep)

    write_json(
        OUTPUT_DIR / "amc_products.json",
        {
            "source": BASE_URL,
            "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "count": len(products),
            "products": products,
        },
    )
    write_json(OUTPUT_DIR / "amc_downloads_index.json", parse_downloads_index(session))
    write_json(OUTPUT_DIR / "amc_reserved_discontinued.json", parse_reserved_page(session))
    write_json(OUTPUT_DIR / "amc_glossary.json", parse_glossary(session))
    print(f"Wrote site metadata to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
