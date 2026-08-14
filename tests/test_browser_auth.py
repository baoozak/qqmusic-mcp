from __future__ import annotations

import pytest

from qqmusic_organizer.browser_auth import cookie_header, cookies_to_dict, login_cookie_is_ready


def test_login_cookie_detection_and_header() -> None:
    cookies = cookies_to_dict(
        [
            {"name": "uin", "value": "o12345678", "domain": ".qq.com"},
            {"name": "qm_keyst", "value": "login-key", "domain": ".qq.com"},
            {"name": "unrelated", "value": "value", "domain": "y.qq.com"},
        ]
    )
    assert login_cookie_is_ready(cookies)
    header = cookie_header(cookies)
    assert "uin=o12345678" in header
    assert "qm_keyst=login-key" in header


@pytest.mark.parametrize(
    "cookies",
    [
        {},
        {"uin": "o12345678"},
        {"qm_keyst": "login-key"},
        {"uin": "not-numeric", "qm_keyst": "login-key"},
    ],
)
def test_incomplete_login_cookie_is_rejected(cookies: dict[str, str]) -> None:
    assert not login_cookie_is_ready(cookies)
    with pytest.raises(ValueError, match="incomplete"):
        cookie_header(cookies)


def test_generic_qq_cookie_does_not_count_as_music_login() -> None:
    assert not login_cookie_is_ready({"uin": "o12345678", "p_skey": "generic-key"})
