from core.crawler import Crawler
from bs4 import BeautifulSoup
import logging
from urllib.parse import urljoin, urlparse
import re
from collections import deque
from ratelimiter import RateLimiter
from core.utils import create_session_with_retries, binary_extensions

class DocsCrawler(Crawler):

    def concat_url_and_href(self, url, href):
        if href.startswith('http'):
            return href
        else:
            if 'index.html?' in href:
                href = href.replace('index.html?', '/')     # special fix for Spark docs
            joined = urljoin(url, href)
            return joined

    def get_url_content(self, url):
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
        }
        with self.rate_limiter:
            response = self.session.get(url, headers=headers)
        if response.status_code != 200:
            logging.info(f"Failed to crawl {url}, response code is {response.status_code}")
            return None, None

        # check for refresh redirect        
        soup = BeautifulSoup(response.content, 'html.parser')
        meta_refresh = soup.find('meta', attrs={'http-equiv': 'refresh'})
        if meta_refresh:
            href = meta_refresh['content'].split('url=')[-1]
            url = self.parse_url_and_href(url, href)
            response = self.session.get(url, headers=headers)
            if response.status_code != 200:
                logging.info(f"Failed to crawl redirect {url}, response code is {response.status_code}")
                return None, None

        page_content = BeautifulSoup(response.content, 'lxml')
        return url, page_content

    def get_urls(self, base_url):
        new_urls = deque([base_url])

        # Crawl each URL in the queue
        while len(new_urls):
            n_urls = len(self.crawled_urls)
            if n_urls>0 and n_urls%100==0:
                logging.info(f"Currently have {n_urls} crawled urls identified")
            
            # pop the left-most URL from new_urls
            url = new_urls.popleft()

            try:
                url, page_content = self.get_url_content(url)
                self.crawled_urls.add(url)

                # Find all the new URLs in the page's content and add them into the queue
                if page_content:
                    for link in page_content.find_all('a'):
                        href = link.get('href')
                        if href is None:
                            continue
                        abs_url = self.concat_url_and_href(url, href)
                        if ((any([r.match(abs_url) for r in self.url_regex])) and                           # match any of the allowed regexes
                            (abs_url.startswith("http")) and                                                # starts with http/https
                            (abs_url not in self.ignored_urls) and                                          # not previously ignored    
                            (len(urlparse(abs_url).fragment)==0) and                                        # does not have fragment
                            (any([abs_url.endswith(ext) for ext in self.extensions_to_ignore])==False)):    # not any of the specified extensions to ignore
                                # add URL if needed
                                if abs_url not in self.crawled_urls and abs_url not in new_urls:
                                    new_urls.append(abs_url)
                        else:
                            self.ignored_urls.add(abs_url)

            except Exception as e:
                import traceback
                logging.info(f"Error crawling {url}: {e}, traceback={traceback.format_exc()}")
                continue

    def crawl(self):
        self.crawled_urls = set()
        self.ignored_urls = set()
        self.extensions_to_ignore = list(set(self.cfg.docs_crawler.extensions_to_ignore + binary_extensions))
        self.url_regex = [re.compile(r) for r in self.cfg.docs_crawler.url_regex]

        self.session = create_session_with_retries()
        self.rate_limiter = RateLimiter(max_calls=2, period=1)

        for base_url in self.cfg.docs_crawler.base_urls:
            self.get_urls(base_url)

        logging.info(f"Found {len(self.crawled_urls)} urls in {self.cfg.docs_crawler.base_urls}")
        source = self.cfg.docs_crawler.docs_system
        for url in self.crawled_urls:
            self.indexer.index_url(url, metadata={'url': url, 'source': source})
            logging.info(f"{source.capitalize()} Crawler: finished indexing {url}")