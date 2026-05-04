"""Tests for issue #1611: /api/sessions must be scoped to the active profile.

Reporter (@stefanpieter) saw multi-profile installs where querying
/api/sessions with `Cookie: hermes_profile=haku` still returned sessions
tagged to other profiles. Two bugs combined to produce this:
  1. Server-side `/api/sessions` had no profile filter — it merged
     WebUI sidecar sessions and CLI/imported sessions and returned the lot.
  2. Frontend `static/sessions.js` filter let every CLI session bypass the
     active-profile filter via `s.is_cli_session || s.profile === active`.

This test file pins the server-side filter shape via api.routes._profiles_match
(the helper used by the /api/sessions and /api/projects handlers) and the
all_profiles=1 opt-in path. End-to-end HTTP-level tests live separately under
tests/test_sessions_endpoint.py if/when added.
"""

from urllib.parse import urlparse

import pytest


# ── _profiles_match helper ─────────────────────────────────────────────────


def test_profiles_match_exact():
    """Same name on both sides matches."""
    from api.routes import _profiles_match
    assert _profiles_match('haku', 'haku') is True
    assert _profiles_match('default', 'default') is True


def test_profiles_match_distinct_named_profiles():
    """Different named profiles do not cross-match."""
    from api.routes import _profiles_match
    assert _profiles_match('haku', 'kinni') is False
    assert _profiles_match('noblepro', 'haku') is False


def test_profiles_match_default_alias_treated_as_root(monkeypatch):
    """A row tagged 'default' matches when the active profile is the renamed
    root (e.g. 'kinni') and vice versa — both resolve to the same ~/.hermes
    home, so they're the same profile from a user perspective."""
    import api.profiles as p
    from api.routes import _profiles_match

    monkeypatch.setattr(p, 'list_profiles_api', lambda: [
        {'name': 'kinni', 'is_default': True, 'path': str(p._DEFAULT_HERMES_HOME)},
    ])
    p._invalidate_root_profile_cache()

    assert _profiles_match('default', 'kinni') is True
    assert _profiles_match('kinni', 'default') is True
    # And neither matches a true named profile
    assert _profiles_match('default', 'haku') is False
    assert _profiles_match('kinni', 'haku') is False


def test_profiles_match_empty_row_treated_as_root():
    """A row with no profile tag (None or empty string) is treated as root.

    Backward compat with legacy sessions/projects that pre-date the profile
    field. The all_sessions() backfill at api/models.py also sets profile
    to 'default' for such rows.
    """
    from api.routes import _profiles_match
    assert _profiles_match(None, 'default') is True
    assert _profiles_match('', 'default') is True
    assert _profiles_match(None, 'haku') is False


def test_profiles_match_active_none_treated_as_default():
    """If active profile resolves to None/empty (boot edge case), treat as 'default'."""
    from api.routes import _profiles_match
    assert _profiles_match('default', None) is True
    assert _profiles_match('default', '') is True


# ── _all_profiles_query_flag ───────────────────────────────────────────────


def test_all_profiles_query_flag_true_values():
    """1, true, yes, on (case-insensitive) all enable aggregate mode."""
    from api.routes import _all_profiles_query_flag
    for v in ('1', 'true', 'TRUE', 'yes', 'YES', 'on'):
        u = urlparse(f'/api/sessions?all_profiles={v}')
        assert _all_profiles_query_flag(u) is True, f"value {v!r} should be true"


def test_all_profiles_query_flag_false_values():
    """0, empty, garbage, missing — all default to scoped mode (False)."""
    from api.routes import _all_profiles_query_flag
    for path in ('/api/sessions', '/api/sessions?all_profiles=0',
                 '/api/sessions?all_profiles=', '/api/sessions?all_profiles=lol'):
        u = urlparse(path)
        assert _all_profiles_query_flag(u) is False, f"path {path!r} should be false"


# ── No client-side CLI bypass ──────────────────────────────────────────────


def test_static_sessions_js_no_cli_session_bypass():
    """static/sessions.js must NOT filter via `s.is_cli_session || s.profile ===`.

    The original bypass let every CLI-imported session leak into the active-profile
    sidebar regardless of which profile owned it. After #1611, the filter is
    solely on `(s.profile||'default') === (S.activeProfile||'default')` — server
    already scoped the wire data, this is defense-in-depth.
    """
    from pathlib import Path

    repo_root = Path(__file__).parent.parent
    src = (repo_root / 'static' / 'sessions.js').read_text(encoding='utf-8')

    assert "s.is_cli_session||s.profile===S.activeProfile" not in src, (
        "Old CLI-session bypass must be removed (#1611)"
    )
    assert "s.is_cli_session || s.profile === S.activeProfile" not in src, (
        "Old CLI-session bypass must be removed (#1611)"
    )
    # And the new shape is present
    assert "(s.profile||'default')===(S.activeProfile||'default')" in src, (
        "Expected the new active-profile-only filter shape"
    )


def test_static_sessions_js_uses_all_profiles_query_when_toggle_on():
    """Frontend must request /api/sessions?all_profiles=1 when _showAllProfiles is true.

    Without this, flipping the toggle just re-renders client-cached rows that
    may not contain cross-profile data (since the server scoped on first fetch).
    """
    from pathlib import Path

    repo_root = Path(__file__).parent.parent
    src = (repo_root / 'static' / 'sessions.js').read_text(encoding='utf-8')

    assert "_showAllProfiles ? '?all_profiles=1' : ''" in src, (
        "Expected fetch path to flip on the toggle state"
    )
    assert "api('/api/sessions' + allProfilesQS)" in src, (
        "Expected /api/sessions fetch to use the variant query"
    )
    assert "api('/api/projects' + allProfilesQS)" in src, (
        "Expected /api/projects fetch to use the variant query"
    )


# ── Cleanup ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _invalidate_profile_cache():
    import api.profiles as p
    p._invalidate_root_profile_cache()
    yield
    p._invalidate_root_profile_cache()
