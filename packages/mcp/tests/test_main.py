from modelforge_mcp.__main__ import main


def test_main_rejects_unknown_server(capsys) -> None:
    assert main(["nope"]) == 2 and "data-profiler" in capsys.readouterr().err
