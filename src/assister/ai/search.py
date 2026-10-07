"""인터넷 검색 (DuckDuckGo).

검색어는 DuckDuckGo 서버로 나간다. 가입도 키도 필요 없는 대신 공식 API가 아니라,
자바스크립트 없이 보는 검색 페이지를 읽는 방식이다. 그쪽이 페이지 모양을 바꾸면 결과를 못 읽는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import httpx

SEARCH_URL = "https://html.duckduckgo.com/html/"
# 누가 보내는 요청인지 그대로 밝힌다. 브라우저인 척하지 않는다.
USER_AGENT = "assister/0.1 (personal desktop assistant)"
REGION = "kr-kr"
TIMEOUT_SEC = 8.0
MAX_HITS = 5
MAX_SNIPPET_CHARS = 300


class SearchError(Exception):
    """검색하지 못했을 때. 메시지는 사용자에게 그대로 보여 줄 수 있는 말이다."""


@dataclass(frozen=True)
class Hit:
    title: str
    url: str
    snippet: str


def _target(href: str) -> str:
    """DuckDuckGo를 거쳐 가는 주소면 그 안의 실제 주소를 꺼낸다."""
    wrapped = parse_qs(urlsplit(href).query).get("uddg")
    return wrapped[0] if wrapped else href


class _Results(HTMLParser):
    """검색 결과 페이지에서 제목, 주소, 요약을 차례로 모은다."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, str]] = []
        self._field = ""  # 지금 읽고 있는 칸. 결과 밖에서는 빈 글

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        found = dict(attrs)
        classes = (found.get("class") or "").split()
        if "result__a" in classes:
            self.rows.append({"title": "", "url": _target(found.get("href") or ""), "snippet": ""})
            self._field = "title"
        elif "result__snippet" in classes and self.rows:
            self._field = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._field = ""

    def handle_data(self, data: str) -> None:
        if self._field:
            self.rows[-1][self._field] += data


def parse_results(page: str, limit: int = MAX_HITS) -> list[Hit]:
    """검색 결과 페이지를 읽는다. 광고는 주소가 DuckDuckGo 안쪽이라 여기서 걸러진다."""
    parser = _Results()
    parser.feed(page)
    hits = [
        Hit(" ".join(row["title"].split()), row["url"], " ".join(row["snippet"].split())[:MAX_SNIPPET_CHARS])
        for row in parser.rows
    ]
    return [hit for hit in hits if hit.title and hit.url.startswith(("http://", "https://"))][:limit]


def search(query: str, *, client: httpx.Client | None = None, limit: int = MAX_HITS) -> list[Hit]:
    """검색 결과를 위에서부터 몇 개 돌려준다. 결과가 없으면 빈 목록, 검색을 못 했으면 SearchError."""
    try:
        # 검색어는 주소에 붙이지 않고 본문으로 보낸다. 주소는 중간 기록에 남기 쉽다.
        response = (client or httpx).post(
            SEARCH_URL, data={"q": query, "kl": REGION}, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SEC
        )
    except httpx.HTTPError as error:
        raise SearchError("인터넷에 연결하지 못했습니다") from error
    if response.status_code != 200:
        # 202는 DuckDuckGo가 자동 요청으로 보고 막았다는 뜻이다. 돌아가는 길을 찾지 않고 그대로 알린다.
        raise SearchError("검색 서비스가 지금은 검색을 받아 주지 않습니다")
    return parse_results(response.text, limit)
