"""A stand-in for what 1DayAPI offers an onboarder token.

The three read-only routes, and the life of an authorization job: made (with a
link), handed a code, asked about, cancelled. It answers as the real platform
does where the app tells answers apart: a token it does not know is refused
with 401, a job is known only to the token that made it, a code that carries
another job's state is refused as `callback_invalid`, and a job is handed over
to be worked on only once.
"""

from typing import Any

from camoufox_pm.core.onboarding import OnboardingError, OneDayClient

TOKEN = "1dk_onb_demo-only"
SECOND = "1dk_onb_second-0002"
LINK = "https://claude.example/oauth/authorize?client_id=demo-only&state="


def upstream(status: int, code: str, message: str) -> OnboardingError:
    """What the app's client makes of an error answer of the platform."""
    failure = OnboardingError(message, 502, "upstream_error")
    failure.upstream_status = status
    failure.upstream_code = code
    return failure


class Platform:
    def __init__(self) -> None:
        self.accepted = {TOKEN: "Shift A", SECOND: "Shift B"}
        self.down = False
        self.asked: list[str] = []
        self.orgs: list[dict[str, Any]] = [
            {"id": "org-a", "name": "Team A", "domains": ["example.com"]},
            {"id": "org-b", "name": "Team B", "domains": ["example.com", "example.net"]},
        ]
        # Every job made, by id; `created` holds what each was asked with.
        self.jobs: dict[str, dict[str, Any]] = {}
        self.created: list[dict[str, Any]] = []
        self.codes: list[tuple[str, str]] = []
        # Set to (status, code, message) to refuse the next job.
        self.refuse_job: tuple[int, str, str] | None = None
        # Accept a code, then fail to answer.
        self.lose_answer = False

    # --- what a test does to a job ------------------------------------------------

    def state_of(self, job_id: str) -> str:
        return self.jobs[job_id]["state"]

    def code_for(self, job_id: str) -> str:
        """What the authorize page shows after consent: the code, a hash sign, the state."""
        return f"code-{job_id}#{self.state_of(job_id)}"

    def finish(self, job_id: str, **result: Any) -> None:
        """The platform is done with a job: onboarded, unless `error` says it failed."""
        job = self.jobs[job_id]
        if "error" in result:
            job.update(status="failed", error=result["error"])
        else:
            job.update(status="done", accountId=result.pop("accountId", f"acct-{job_id}"), **result)

    def last_job(self) -> str:
        return list(self.jobs)[-1]

    # --- the routes -----------------------------------------------------------------

    def _out(self, job: dict[str, Any]) -> dict[str, Any]:
        shown = {key: value for key, value in job.items() if key not in ("state", "owner")}
        if job["status"] != "pending_callback":
            shown.pop("authorizeUrl", None)
        if "error" in shown:
            # An onboarder is told the code and a general sentence, nothing more.
            code = str(shown["error"]).split(":")[0]
            shown["error"] = f"{code}: account authorization could not be completed"
        return shown

    def _job(self, client: OneDayClient, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None or job["owner"] != client.token:
            raise upstream(404, "not_found", "onboarding_job not found")
        return job

    async def request(self, client: OneDayClient, method: str, path: str, body: Any = None) -> Any:
        self.asked.append(path)
        if self.down:
            raise OnboardingError("无法连接 1dayApi：ConnectError", 503, "oneday_unavailable")
        if client.token not in self.accepted:
            raise upstream(401, "invalid_onboard_token", "Invalid or revoked onboarder token")
        route = path.split("?")[0]
        if route == "/api/onboard/me":
            nodes = [
                {"nodeId": "direct-1", "name": None, "online": True, "capacity": 20, "used": 13}
            ]
            return {"label": self.accepted[client.token], "nodes": nodes, "orgs": self.orgs}
        if route == "/api/onboard/node-groups":
            return [{"group": "UK", "total": 3, "live": 3, "assignable": 2}]
        if route == "/api/onboard/proxies":
            return [
                {
                    "id": "p18",
                    "group": "UK",
                    "host": "proxy.internal",
                    "port": 8080,
                    "exitIp": "203.0.113.18",
                    "overflowSlots": 2,
                },
                {
                    "id": "p17",
                    "group": "UK",
                    "host": "proxy.internal",
                    "port": 8081,
                    "exitIp": "203.0.113.17",
                    "overflowSlots": 3,
                },
            ]
        if route == "/api/onboard/jobs" and method == "POST":
            assert body["method"] == "auth_url", "only authorize links are asked for"
            if "jobId" in body:
                return self._hand_over(client, body)
            return self._create(client, body)
        if route.endswith("/cancel") and method == "POST":
            job = self._job(client, route.split("/")[-2])
            if job["status"] in ("pending_callback", "working"):
                job.update(status="failed", error="job_cancelled: cancelled by the operator")
            return self._out(job)
        if route.startswith("/api/onboard/jobs/") and method == "GET":
            return self._out(self._job(client, route.split("/")[-1]))
        raise AssertionError(
            f"the app asked for a route the platform does not have: {method} {path}"
        )

    def _create(self, client: OneDayClient, body: dict[str, Any]) -> dict[str, Any]:
        if self.refuse_job is not None:
            refusal, self.refuse_job = self.refuse_job, None
            raise upstream(*refusal)
        number = len(self.jobs) + 1
        job_id, state = f"job-{number}", f"state-{number}"
        self.created.append(body)
        self.jobs[job_id] = {
            "jobId": job_id,
            "method": "auth_url",
            "nodeId": body.get("nodeId"),
            "status": "pending_callback",
            "expiresAt": "2030-01-01T00:30:00Z",
            "authorizeUrl": LINK + state,
            "state": state,
            "owner": client.token,
        }
        return {"jobId": job_id, "status": "pending_callback", "authorizeUrl": LINK + state}

    def _hand_over(self, client: OneDayClient, body: dict[str, Any]) -> dict[str, Any]:
        job = self._job(client, body["jobId"])
        if job["status"] != "pending_callback":
            raise upstream(400, "invalid_request", "Job is not waiting for a callback URL")
        pasted = str(body["callbackUrl"])
        if "#" in pasted and pasted.split("#", 1)[1] != job["state"]:
            raise upstream(
                400, "callback_invalid", "Callback URL cannot be parsed or its state does not match"
            )
        self.codes.append((job["jobId"], pasted))
        job.update(status="working", expiresAt="2030-01-01T00:03:00Z")
        if self.lose_answer:
            self.lose_answer = False
            raise OnboardingError("无法连接 1dayApi：ReadTimeout", 503, "oneday_unavailable")
        return {"jobId": job["jobId"], "status": "working"}


def install(monkeypatch) -> Platform:
    """Put a stand-in in the place of the platform, for the test that asks."""
    stand_in = Platform()

    async def request(self, method, path, body=None):
        return await stand_in.request(self, method, path, body)

    monkeypatch.setattr(OneDayClient, "request", request)
    return stand_in
