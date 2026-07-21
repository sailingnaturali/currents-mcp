from currents_mcp.tools import list_gates


def test_list_gates_reports_coverage():
    result = list_gates()
    dests = {c["destination"] for c in result["coverage"]}
    assert "Nanaimo" in dests
    assert "Friday Harbor" in dests
    assert isinstance(result["display"], str) and "Nanaimo" in result["display"]
    # Gates route by registry key internally; coverage renders display names.
    assert "Dodd Narrows" in result["display"] and "chs-" not in result["display"]
    assert all("chs-" not in g for c in result["coverage"] for g in c["gates"])
    # Open-water destinations (empty gate list) render the "no gates" fallback.
    assert "no gates" in result["display"]
