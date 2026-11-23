"""Accessibility basics that screen readers and keyboard users rely on."""
import re

import pytest

PAGES = ["/", "/projects/", "/feedback/", "/habits/", "/tasks/", "/checkin/", "/goals/", "/review/", "/dashboard/",
         "/settings/", "/records/", "/insights/", "/recap/"]


@pytest.mark.parametrize("page", PAGES)
def test_page_landmarks_and_labels(client, auth, page):
    auth.register_and_login()
    html = client.get(page).get_data(as_text=True)
    assert '<a href="#main" class="skip-link">' in html
    assert '<main id="main"' in html
    assert '<button type="button" class="hamburger"' in html and 'aria-expanded="false"' in html
    if page != "/recap/":                       # the recap isn't in the menu
        assert 'aria-current="page"' in html
    assert len(re.findall(r"<h1[ >]", html)) == 1, "exactly one main heading"
    # every select and text box has a label of some kind
    for found in re.finditer(r"<(?:select|textarea)\b[^>]*>", html):
        tag, before = found.group(0), html[:found.start()]
        name = re.search(r'\bid="([^"]+)"', tag)
        inside_label = before.rfind("<label") > before.rfind("</label>")
        assert "aria-label" in tag or inside_label or (name and f'for="{name.group(1)}"' in html), tag
    assert 'role="button"' not in html


def test_login_page_basics(client):
    html = client.get("/auth/login").get_data(as_text=True)
    assert '<html lang="' in html and '<main id="main"' in html
