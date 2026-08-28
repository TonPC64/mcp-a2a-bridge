from run_copilot_main_dev import CopilotResult, build_card, format_reply


def test_card_advertises_streaming_and_the_delegation_skill():
    card = build_card(9010)
    assert card.capabilities.streaming is True
    assert card.supported_interfaces[0].url == "http://127.0.0.1:9010/"
    assert card.skills[0].id == "delegate-coding-task"


def test_card_examples_show_the_cwd_convention():
    assert any(example.startswith("cwd:") for example in build_card(9010).skills[0].examples)


def test_format_reply_uses_copilot_text():
    assert format_reply(CopilotResult(text="done", exit_code=0)) == "done"


def test_format_reply_lists_modified_files():
    reply = format_reply(CopilotResult(text="Added the flag.", exit_code=0, files_modified=["a.py", "b.py"]))
    assert "Added the flag." in reply and "- a.py" in reply and "- b.py" in reply


def test_format_reply_never_returns_empty_text():
    assert format_reply(CopilotResult(text="   ", exit_code=0)).strip()
