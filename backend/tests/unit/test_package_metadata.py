from importlib.metadata import version

import automation_tool


def test_package_exposes_the_installed_distribution_version() -> None:
    assert automation_tool.__version__ == version("automation-tool")
    assert automation_tool.__version__ == "0.1.0"
