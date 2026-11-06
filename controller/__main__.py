"""CLI entry point.

    python -m controller run --url http://127.0.0.1:8700        # what run.sh does
    python -m controller status --db state/<scenario>.sqlite     # supervisor one-screen view
    python -m controller why --db ... --wf nw_renewal --at "D3 14:00"
    python -m controller log --db ... [--wf X] [--kind proposal] [--limit 50]
    python -m controller health --db ...                         # loop's own health (ingest, lag, credits)
"""
import argparse
import sys

from . import audit


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="controller")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--url", required=True)
    r.add_argument("--state-dir", default=None)
    r.add_argument("--max-ticks", type=int, default=None, help="exit after N ticks (testing crash recovery)")
    for name in ("status", "why", "log", "health"):
        p = sub.add_parser(name)
        p.add_argument("--db", required=True)
        p.add_argument("--wf", default=None)
        if name == "why":
            p.add_argument("--at", required=True)
        if name == "log":
            p.add_argument("--kind", default=None)
            p.add_argument("--limit", type=int, default=60)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        from .loop import Controller
        Controller(a.url, a.state_dir, a.max_ticks).run()
    elif a.cmd == "status":
        print(audit.status(a.db, a.wf))
    elif a.cmd == "why":
        print(audit.why(a.db, a.wf, a.at))
    elif a.cmd == "log":
        print(audit.decisions(a.db, a.wf, a.kind, a.limit))
    elif a.cmd == "health":
        print(audit.loop_health(a.db))
    return 0


if __name__ == "__main__":
    sys.exit(main())
