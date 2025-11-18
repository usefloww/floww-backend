import sentry_sdk

from app.settings import settings


def init_sentry():
    if settings.SENTRY_DSN:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            traces_sample_rate=0.01,
            profiles_sample_rate=0.01,
            enable_tracing=True,
            send_default_pii=False,
            environment=settings.SENTRY_ENVIRONMENT,
        )
