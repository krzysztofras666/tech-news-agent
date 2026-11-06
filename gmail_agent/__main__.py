"""Entry point so `python -m gmail_agent ...` works."""

import warnings

# These are noisy and harmless: Python 3.9 EOL nag and google-api-core's own
# deprecation warning about Python <3.10, plus urllib3's "LibreSSL not OpenSSL"
# warning on macOS. Silence them by module/message so we don't have to import
# the warning classes (which would themselves trigger the warning we want to
# suppress).
warnings.filterwarnings("ignore", category=FutureWarning, module=r"google\..*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"google\..*")
warnings.filterwarnings("ignore", message=r".*OpenSSL 1\.1\.1\+.*")

from .cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
