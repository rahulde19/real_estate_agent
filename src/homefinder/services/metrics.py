"""Per-run LLM usage meter (Phase 6.1): calls and tokens, read from the Gemini responses.

One process-wide meter, reset at the start of each graph run. The UI runs one search at a time, so a global is enough.
"""
import threading


class RunMeter:
    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.calls = {"extract": 0, "explain": 0}
            self.tokens_in = {"extract": 0, "explain": 0}
            self.tokens_out = {"extract": 0, "explain": 0}

    def add(self, kind: str, reply) -> None:
        """Record one LLM call. `reply` is a LangChain AIMessage; token counts may be missing."""
        usage = getattr(reply, "usage_metadata", None) or {}
        with self._lock:
            self.calls[kind] += 1
            self.tokens_in[kind] += int(usage.get("input_tokens") or 0)
            self.tokens_out[kind] += int(usage.get("output_tokens") or 0)

    def snapshot(self) -> dict:
        with self._lock:
            return {"calls": dict(self.calls), "tokens_in": dict(self.tokens_in), "tokens_out": dict(self.tokens_out)}


METER = RunMeter()
