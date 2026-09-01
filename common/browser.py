"""Attach to an already-running Chrome instead of launching a driver.

This is deliberate, not a shortcut. Launching a fresh Selenium browser means a
clean profile with no session, which fails immediately on any surface that
requires a login, and trips bot detection on several that do not. Attaching to
a Chrome the user started themselves reuses their real logged-in session and
looks like exactly what it is: a person's browser.

The user starts Chrome once::

    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" ^
        --remote-debugging-port=9222 --user-data-dir="C:\\chrome-debug"

``--user-data-dir`` matters. Without it Chrome refuses to open the debug port on
an existing profile, and the flag appears to do nothing.

**Untested code.** Everything here needs a live browser, so it is excluded from
the test suite by design. That is why it is this thin: the module's whole job is
to hand back a driver, so that the logic wrapped around it -- which is where the
bugs live -- can be tested without one.
"""

from __future__ import annotations

import os

DEFAULT_PORT = 9222
#: Attaching enumerates every open tab, so a browser with fifty tabs can take
#: a while to hand back a session. The default 60s is not enough.
DEFAULT_TIMEOUT = 180


class ChromeNotRunning(RuntimeError):
    """Nothing is listening on the debug port."""


class ChromeDriverMissing(RuntimeError):
    """chromedriver.exe is not next to the script."""


def chromedriver_path(script_dir: str) -> str:
    """Resolve chromedriver relative to the script, with a usable error.

    Relative to the script rather than the working directory, because these are
    run by double-clicking or from whatever directory the terminal happened to
    be in.
    """
    path = os.path.join(script_dir, "chromedriver.exe")
    if not os.path.exists(path):
        raise ChromeDriverMissing(
            "chromedriver.exe not found at %s\n\n"
            "Download the build matching your Chrome version from\n"
            "  https://googlechromelabs.github.io/chrome-for-testing/\n"
            "and put it next to this script. Check your Chrome version at\n"
            "chrome://settings/help -- a mismatched major version fails with a\n"
            "'this version of ChromeDriver only supports Chrome version N'\n"
            "message that names both numbers." % path
        )
    return path


def attach_to_chrome(script_dir: str, port: int = DEFAULT_PORT,
                     timeout: int = DEFAULT_TIMEOUT):
    """Return a Selenium driver attached to a running Chrome.

    Raises :class:`ChromeNotRunning` with the exact command to run, because the
    failure is always the same and the fix is always the same.
    """
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.chrome.service import Service

    options = webdriver.ChromeOptions()
    options.add_experimental_option("debuggerAddress", "127.0.0.1:%d" % port)

    service = Service(executable_path=chromedriver_path(script_dir))
    try:
        driver = webdriver.Chrome(service=service, options=options)
    except WebDriverException as exc:
        raise ChromeNotRunning(
            "Could not attach to Chrome on port %d.\n\n"
            "Start Chrome with the debug port open first:\n"
            '  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
            '--remote-debugging-port=%d --user-data-dir="C:\\chrome-debug"\n\n'
            "If Chrome IS running with that flag, it is probably the tab count "
            "-- attaching enumerates every open tab and times out on a browser "
            "with dozens. Close what you do not need and try again.\n\n"
            "Original error: %s" % (port, port, exc)
        ) from exc

    driver.set_page_load_timeout(timeout)
    return driver


def survives_disconnect(function):
    """Turn a mid-run browser disconnect into a clean exit, not a traceback.

    Chrome drops the connection on long jobs -- an update, a crash, the user
    closing the window. Every long-running script wraps its main loop in this so
    the checkpoint is saved and the user is told to rerun, rather than losing
    forty minutes to a stack trace.
    """
    from functools import wraps

    @wraps(function)
    def wrapper(*args, **kwargs):
        from selenium.common.exceptions import WebDriverException
        try:
            return function(*args, **kwargs)
        except WebDriverException as exc:
            print("\nThe browser connection was lost (%s)."
                  % type(exc).__name__)
            print("Progress has been saved. Restart Chrome with the debug "
                  "port open and rerun this script -- it will skip everything "
                  "already done.")
            return 1

    return wrapper
