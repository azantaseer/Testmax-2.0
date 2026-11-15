"""Thin client for the TestMax server's runner API. The runner never talks to an AI provider directly."""
import httpx


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class ServerClient:
    def __init__(self, server: str, key: str | None = None, timeout: float = 30.0):
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self.http = httpx.Client(base_url=server.rstrip("/"), headers=headers, timeout=timeout)

    def _call(self, method: str, path: str, body: dict | None = None, timeout: float | None = None):
        try:
            response = self.http.request(method, path, json=body if body is not None else {}, timeout=timeout)
        except httpx.HTTPError as exc:
            raise ApiError(0, "network", f"Could not reach the server ({type(exc).__name__}).") from None
        if response.status_code == 204:
            return None
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            err = (data or {}).get("error", {})
            raise ApiError(response.status_code, err.get("code", "error"), err.get("message", f"HTTP {response.status_code}"))
        return data

    # pairing (no key yet)
    def pair_start(self, name: str, capacity: int, os_name: str, version: str):
        return self._call("POST", "/api/runner/pair/start", {"name": name, "capacity": capacity, "os": os_name, "version": version})

    def pair_poll(self, device_code: str):
        return self._call("POST", "/api/runner/pair/poll", {"device_code": device_code})

    # normal operation
    def heartbeat(self, capacity: int, os_name: str, version: str):
        return self._call("POST", "/api/runner/heartbeat", {"capacity": capacity, "os": os_name, "version": version})

    def claim(self, free_slots: int):
        return self._call("POST", "/api/runner/claim", {"free_slots": free_slots})["job"]

    def job_heartbeat(self, job_id: str):
        return self._call("POST", f"/api/runner/jobs/{job_id}/heartbeat")

    def progress(self, job_id: str, message: str):
        return self._call("POST", f"/api/runner/jobs/{job_id}/progress", {"message": message[:300]})

    def complete(self, job_id: str, result: dict):
        return self._call("POST", f"/api/runner/jobs/{job_id}/complete", {"result": result}, timeout=120)

    def fail(self, job_id: str, message: str, retryable: bool = False):
        return self._call("POST", f"/api/runner/jobs/{job_id}/fail", {"message": message[:1000], "retryable": retryable})

    def llm(self, job_id: str, messages: list, tools: list, tool_choice: str | None = None):
        return self._call("POST", "/api/runner/llm", {"job_id": job_id, "messages": messages, "tools": tools, "tool_choice": tool_choice}, timeout=330)
