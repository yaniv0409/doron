from agent_platform.infrastructure.browser import clean_text_from_html, extract_links_from_html, select_main_html


HTML = """
<html>
  <body>
    <nav><a href="/nav">Nav Link</a></nav>
    <main>
      <h1>Headline</h1>
      <p>Hello <strong>world</strong>.</p>
      <a href="/first" title="First Link">First</a>
      <a href="https://example.com/second">Second</a>
      <a href="/first">Duplicate</a>
    </main>
  </body>
</html>
"""


def test_select_main_html_prefers_main_container() -> None:
    selected = select_main_html(HTML, extract_main_content_only=True)

    assert "<main>" in selected
    assert "Nav Link" not in selected


def test_clean_text_from_html_removes_tags() -> None:
    selected = select_main_html(HTML, extract_main_content_only=True)
    text = clean_text_from_html(selected, 10_000)

    assert "<strong>" not in text
    assert "Headline" in text
    assert "Hello" in text


def test_extract_links_returns_structured_unique_absolute_links() -> None:
    selected = select_main_html(HTML, extract_main_content_only=True)
    links = extract_links_from_html(
        selected,
        base_url="https://example.com/page",
        max_links=20,
    )

    assert [item.href for item in links] == [
        "https://example.com/first",
        "https://example.com/second",
    ]
    assert links[0].text == "First"
    assert links[0].title == "First Link"
