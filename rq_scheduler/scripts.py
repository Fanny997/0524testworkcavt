from __future__ import annotations


class _RqschedulerShim:
    def main(self) -> None:
        raise RuntimeError(
            "rq-scheduler is not installed in this environment. "
            "The local shim only provides import compatibility."
        )


rqscheduler = _RqschedulerShim()
