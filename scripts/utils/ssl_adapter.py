import logging
import ssl

from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

logger = logging.getLogger("renovate-operation-check.utils.ssl_adapter")


class RelaxedX509StrictAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def mount_if_enabled(session, enabled, component):
    """Mount the relaxed adapter on a session when configured; log the deviation."""
    if enabled:
        logger.warning(
            f"[SEC] {component}: TLS X.509 strict verification relaxed via "
            "relax_x509_strict)"
            "verification remain active"
        )
        session.mount("https://", RelaxedX509StrictAdapter())
    return session
