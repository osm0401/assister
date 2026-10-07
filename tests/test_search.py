import httpx
import pytest

from assister.ai.search import USER_AGENT, Hit, SearchError, parse_results, search

PAGE = """
<div class="result results_links web-result">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="https://www.weather.go.kr/w/index.do">기상청 <b>날씨</b>누리</a>
  </h2>
  <a class="result__url" href="https://www.weather.go.kr/w/index.do"> www.weather.go.kr </a>
  <a class="result__snippet" href="https://www.weather.go.kr/w/index.do">서울 <b>오늘</b> 맑음,
     낮 최고 21도</a>
</div>
<div class="result results_links result--ad">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/y.js?ad_domain=example.com">광고</a>
  <a class="result__snippet" href="//duckduckgo.com/y.js?ad_domain=example.com">사세요</a>
</div>
<div class="result results_links web-result">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1&amp;rut=abc">둘째 결과</a>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com">둘째 요약</a>
</div>
"""


def searching_with(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_results_are_read_from_the_page_without_ads():
    assert parse_results(PAGE) == [
        Hit("기상청 날씨누리", "https://www.weather.go.kr/w/index.do", "서울 오늘 맑음, 낮 최고 21도"),
        Hit("둘째 결과", "https://example.com/a?b=1", "둘째 요약"),  # DuckDuckGo를 거쳐 가는 주소는 실제 주소로
    ]
    assert len(parse_results(PAGE, limit=1)) == 1
    assert parse_results("<html><body>결과 없음</body></html>") == []


def test_search_sends_the_query_in_the_body_and_says_who_it_is():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=PAGE)

    hits = search("서울 오늘 날씨", client=searching_with(handler))

    assert [hit.title for hit in hits] == ["기상청 날씨누리", "둘째 결과"]
    request = seen[0]
    assert request.method == "POST" and not request.url.query  # 검색어를 주소에 남기지 않는다
    assert "q=%EC%84%9C%EC%9A%B8" in request.content.decode()
    assert request.headers["user-agent"] == USER_AGENT  # 브라우저인 척하지 않는다


def test_search_reports_being_refused_or_offline():
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network")

    # 202는 DuckDuckGo가 자동 요청으로 보고 막았을 때 온다.
    with pytest.raises(SearchError):
        search("날씨", client=searching_with(lambda request: httpx.Response(202, text="<html>anomaly</html>")))
    with pytest.raises(SearchError):
        search("날씨", client=searching_with(offline))
