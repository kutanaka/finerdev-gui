"""Take or block devgui's operator right from an external program
(docs/design.md section 9.5) - e.g. a measurement script that must not
have a GUI user change the instruments underneath it:

    from devgui.priority import priority

    priority("get", block=True, force=True)   # GUI can't operate or even request
    try:
        run_measurement()
    finally:
        priority("release")

- `force=True`: take the right immediately, whoever holds it (the GUI
  holder is told it was revoked). `force=False` fails with PriorityError
  if a GUI user holds it.
- `block=True`: until `priority("release")`, every GUI acquire/request is
  refused immediately. With `block=False`, a GUI user can still request
  the right and gets it after the usual takeover wait, since nobody is
  there to answer.
- An external hold never times out; only "release" ends it (it also
  lifts the block).
- `priority("release")` only works for whoever did the "get": the token
  "get" returned is remembered in this process and sent back (or pass
  `token=` explicitly).
- `priority("release", force=True)`: free the right immediately and
  unconditionally, whoever holds it - a GUI user or a different external
  program - also lifting any block.

Standard library only, so any Python that can import this file can use
it. Also usable from a shell; each command is its own process, so a
shell "release" needs --force (e.g. to unblock after a crashed script)
or the --token printed by "get":

    python -m devgui.priority get --block --force
    python -m devgui.priority release --force

The server only accepts these calls from its `priority_hosts` setting
(loopback by default), so run this on the devgui machine itself.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_URL = "http://localhost:8000"


class PriorityError(Exception):
    """The server refused or couldn't be reached."""


# Token from this process's last successful "get", for a later "release".
_held_token: str | None = None


def priority(
    action: str,
    *,
    block: bool = False,
    force: bool = False,
    name: str | None = None,
    token: str | None = None,
    url: str = DEFAULT_URL,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """`action` is "get" (take the right) or "release". `name` is shown
    to GUI users as the holder (default: "外部プログラム"). `token` is
    for "release" only (default: the one this process's "get" received);
    it's ignored with force=True."""
    global _held_token
    if action == "get":
        payload: dict[str, Any] = {"block": block, "force": force, "name": name}
    elif action == "release":
        payload = {"force": force, "token": token or _held_token}
    else:
        raise ValueError(f"action must be 'get' or 'release', got {action!r}")

    request = urllib.request.Request(
        f"{url.rstrip('/')}/api/priority/{action}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get("detail", exc.reason)
        except ValueError:
            detail = exc.reason
        raise PriorityError(f"{action}: HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise PriorityError(f"{action}: cannot reach devgui at {url}: {exc.reason}") from None
    _held_token = result.get("token") if action == "get" else None
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m devgui.priority")
    parser.add_argument("action", choices=["get", "release"])
    parser.add_argument("--block", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--name", default=None)
    parser.add_argument("--token", default=None, help="release: the token 'get' printed")
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args(argv)
    try:
        result = priority(
            args.action,
            block=args.block,
            force=args.force,
            name=args.name,
            token=args.token,
            url=args.url,
        )
    except PriorityError as exc:
        print(f"devgui.priority: {exc}", file=sys.stderr)
        return 1
    if args.action == "get":
        print(result["token"])  # for a later "release --token ..."
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
