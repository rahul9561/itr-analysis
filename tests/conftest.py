import socket

import pytest


class OutboundNetworkAttempt(AssertionError):
    pass


NETWORK_ATTEMPTS: list[tuple] = []


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail any test that tries to open an outbound network connection."""

    def guard(*args, **kwargs):
        NETWORK_ATTEMPTS.append(args)
        raise OutboundNetworkAttempt(f"Outbound network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket, "getaddrinfo", guard)
    yield


def pytest_configure(config):
    try:
        import django
        import rest_framework  # noqa: F401
    except ImportError:
        return
    from django.conf import settings

    if settings.configured:
        return
    settings.configure(
        SECRET_KEY="test-only-not-secret",
        DEBUG=False,
        ALLOWED_HOSTS=["testserver"],
        USE_TZ=True,
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "rest_framework",
            "itr_analysis_engine.django_app",
        ],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        ROOT_URLCONF="tests.urls",
        MIDDLEWARE=["django.middleware.common.CommonMiddleware"],
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        REST_FRAMEWORK={
            "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
        },
        ITR_ENGINE={"THROTTLE_RATE": None},
    )
    django.setup()
