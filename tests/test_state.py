from ccmux_state.state import Blocked, Dead, Idle, Working


def test_idle_default_text_is_empty():
    assert Idle().text == ""


def test_idle_with_text():
    assert Idle(text="✻ Churned for 55s").text == "✻ Churned for 55s"


def test_working_default_text_is_empty():
    assert Working().text == ""


def test_blocked_requires_tool_name():
    b = Blocked(tool_name="AskUserQuestion")
    assert b.tool_name == "AskUserQuestion"
    assert b.content == ""


def test_blocked_with_content():
    b = Blocked(tool_name="Bash", content="Read(/etc/passwd)")
    assert b.content == "Read(/etc/passwd)"


def test_dead_carries_last_state():
    d = Dead(reason="session_end", last_state=Idle(text="bye"))
    assert d.reason == "session_end"
    assert d.last_state == Idle(text="bye")


def test_states_are_distinct_by_type():
    assert Idle() != Working()
    assert Working(text="x") != Idle(text="x")


def test_states_are_value_equal():
    assert Idle(text="a") == Idle(text="a")
    assert Working(text="b") == Working(text="b")
    assert Blocked(tool_name="t", content="c") == Blocked(tool_name="t", content="c")
