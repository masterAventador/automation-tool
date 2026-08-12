"""Module entry used by source runs and the PyInstaller bundle.

Runs the merged local service: control-plane HTTP and the executor loop in
one process, speaking the executor's stdin/stdout protocol to the App.
"""

from automation_tool.local_service import main  # pragma: no cover - verified in a child process

if __name__ == "__main__":  # pragma: no cover - verified in a child process
    main()  # pragma: no cover - verified in a child process
