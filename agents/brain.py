import itertools
import json
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error


def _get_host_ip() -> str:
    """Detect the host IP via the default gateway in the routing table."""
    result = subprocess.run(
        ["ip", "route", "show", "default"],
        capture_output=True,
        text=True,
    )
    # Output format: "default via <IP> dev <iface> ..."
    parts = result.stdout.split()
    try:
        return parts[parts.index("via") + 1]
    except (ValueError, IndexError):
        raise RuntimeError(f"Could not detect default gateway: {result.stdout!r}")


HOST_IP = _get_host_ip()
OLLAMA_URL = f"http://{HOST_IP}:11434/api/generate"
MODEL = "gemma3:latest"

# Typical generation time for context — shown to the user
_EXPECTED_SECONDS = 120


class _Spinner:
    """Displays an animated spinner + elapsed time on stderr while a task runs."""

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, label: str = "Waiting for Gemma"):
        self.label = label
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self) -> None:
        start = time.time()
        for frame in itertools.cycle(self.FRAMES):
            if self._stop_event.is_set():
                break
            elapsed = int(time.time() - start)
            hint = f"(usually ~{_EXPECTED_SECONDS}s)" if elapsed < 10 else f"elapsed: {elapsed}s"
            sys.stderr.write(f"\r  {frame} {self.label} … {hint}   ")
            sys.stderr.flush()
            time.sleep(0.1)
        # Clear the spinner line
        sys.stderr.write("\r" + " " * 60 + "\r")
        sys.stderr.flush()

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop_event.set()
        self._thread.join()


def ask_gemma(system_prompt: str, user_prompt: str, label: str = "Waiting for Gemma") -> str:
    """Send a prompt to the local Gemma model and return the response text.

    Displays a live spinner + elapsed time on stderr while waiting.
    """
    payload = json.dumps({
        "model": MODEL,
        "system": system_prompt,
        "prompt": user_prompt,
        "stream": False,
    }).encode()

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with _Spinner(label):
            with urllib.request.urlopen(req, timeout=600) as resp:
                body = json.loads(resp.read().decode())
                return body.get("response", "")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Failed to reach Ollama at {OLLAMA_URL}: {e}") from e


if __name__ == "__main__":
    print(f"Detected host IP: {HOST_IP}")
    print(f"Querying {OLLAMA_URL} ...\n")
    answer = ask_gemma(
        system_prompt="You are a helpful Linux expert.",
        user_prompt="Why is Ubuntu the best Linux distro?",
        label="Thinking",
    )
    print(answer)
