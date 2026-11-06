"""HTTP client for the simulator. This module is the whole contract surface (docs/SIMULATOR.md).

Retry policy:
- connection errors / 5xx on idempotent calls (GETs, and POST/PUT that carry an idempotency key) are
  retried with backoff;
- POST /sim/advance is NOT blindly retried: on a transport error we re-read /sim/state and only re-issue
  the advance if simulated time did not move (otherwise we'd skip time twice);
- 4xx are returned to the caller as ApiError with the simulator's error code.
"""
from __future__ import annotations

import json
import time

import httpx


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail):
        super().__init__(f"{status} {code}: {detail}")
        self.status, self.code, self.detail = status, code, detail


class SimClient:
    def __init__(self, base_url: str, timeout: float = 600.0, retries: int = 8):
        self.base = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base, timeout=timeout)
        self.retries = retries
        self.calls = 0

    # ------------------------------------------------------------------ core
    def _req(self, method: str, path: str, *, retry: bool = True, **kw):
        delay = 0.5
        last = None
        for attempt in range(self.retries if retry else 1):
            try:
                self.calls += 1
                r = self.http.request(method, path, **kw)
            except httpx.TransportError as e:
                last = e
                time.sleep(delay)
                delay = min(delay * 2, 10)
                continue
            if r.status_code >= 500:
                last = ApiError(r.status_code, "server_error", r.text[:200])
                time.sleep(delay)
                delay = min(delay * 2, 10)
                continue
            if r.status_code >= 400:
                try:
                    body = r.json()
                    raise ApiError(r.status_code, body.get("error", "error"), body.get("detail"))
                except (ValueError, json.JSONDecodeError):
                    raise ApiError(r.status_code, "error", r.text[:200])
            return r.json()
        if isinstance(last, ApiError):
            raise last
        raise ConnectionError(f"{method} {path} failed after retries: {last}")

    def get(self, path: str, **params):
        return self._req("GET", path, params=params or None)

    # ------------------------------------------------------------------ endpoints
    def state(self):
        return self.get("/sim/state")

    def advance(self, minutes: int):
        before = self.state()["sim_minute"]
        try:
            return self._req("POST", "/sim/advance", json={"minutes": minutes}, retry=False)
        except (httpx.TransportError, ConnectionError, ApiError) as e:
            if isinstance(e, ApiError) and e.status < 500:
                raise
            # Find out whether the advance happened before deciding to re-issue it.
            for _ in range(self.retries):
                try:
                    st = self.state()
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(2)
            else:
                raise
            if st["sim_minute"] != before:
                st["new_chunks"] = None  # caller must list chunks after its watermark
                return st
            return self._req("POST", "/sim/advance", json={"minutes": minutes}, retry=False)

    def list_chunks(self, kind: str, after: int | None = None):
        params = {"kind": kind}
        if after is not None:
            params["after"] = after
        return self.get("/events/chunks", **params)

    def stream_chunk(self, n: int):
        """Yield raw NDJSON lines of chunk n without holding the whole chunk in memory.

        No retry in here: resuming mid-stream would replay lines. The ingestor aggregates into a scratch
        accumulator and retries the WHOLE chunk on failure (nothing is committed until the chunk ends)."""
        with self.http.stream("GET", f"/events/chunks/{n}") as r:
            if r.status_code >= 400:
                raise ApiError(r.status_code, "chunk_error", r.read()[:200])
            for line in r.iter_lines():
                if line:
                    yield line

    def workflows(self):
        return self.get("/workflows")

    def workflow(self, wf: str):
        return self.get(f"/workflows/{wf}")

    def config(self, wf: str):
        return self.get(f"/workflows/{wf}/config")

    def config_history(self, wf: str):
        return self.get(f"/workflows/{wf}/config/history")

    def request_approval(self, body: dict):
        return self._req("POST", "/approvals", json=body)

    def approval(self, aid: str):
        return self.get(f"/approvals/{aid}")

    def approvals(self, wf: str | None = None, status: str | None = None):
        p = {}
        if wf:
            p["workflow_id"] = wf
        if status:
            p["status"] = status
        return self.get("/approvals", **p)

    def apply(self, wf: str, approval_id: str):
        return self._req("POST", f"/workflows/{wf}/apply", json={"approval_id": approval_id})

    def auto_apply(self, wf: str, body: dict):
        return self._req("PUT", f"/workflows/{wf}/config", json=body)

    def suggest_fixes(self, wf: str, problem: str):
        return self._req("POST", "/llm/suggest-fixes", json={"workflow_id": wf, "problem": problem}, retry=False)

    def validate(self, wf: str, prompt: str, n: int):
        return self._req("POST", "/validation/runs", json={"workflow_id": wf, "prompt_version": prompt, "n": n},
                         retry=False)
