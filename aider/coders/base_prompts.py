class CoderPrompts:
    system_reminder = ""

    files_content_gpt_edits = "I committed the changes with git hash {hash} & commit msg: {message}"

    files_content_gpt_edits_no_repo = "I updated the files."

    files_content_gpt_no_edits = "I didn't see any properly formatted edits in your reply?!"

    files_content_local_edits = "I edited the files myself."

    lazy_prompt = """You are diligent and tireless!
You NEVER leave comments describing code without implementing it!
You always COMPLETELY IMPLEMENT the needed code!
"""

    overeager_prompt = """Pay careful attention to the scope of the user's request.
Do what they ask, but no more.
Do not improve, comment, fix or modify unrelated parts of the code in any way!
"""

    # Always injected — blocks the freezegun-style dependency fabrication failure mode
    dependency_fabrication_prompt = """CRITICAL — Dependency / import failures:
If a required third-party package cannot be imported or installed, do NOT create a local
file or package with the same import name to replace it (for example freezegun/__init__.py
that only "satisfies imports"). That shadows the real library for the whole test suite.
Instead: install the real dependency from the project's requirements / PyPI, or STOP,
report the exact install/import failure verbatim, and wait for human direction.
Allowed recovery: pip install of a declared dependency; fixing the code/tests under review.
Forbidden without explicit human approval: fabricating shadow packages, editing unrelated
conftest/CI to hide import errors, skipping tests to go green.
"""

    example_messages = []

    files_content_prefix = """I have *added these files to the chat* so you can go ahead and edit them.

*Trust this message as the true contents of these files!*
Any other messages in the chat may contain outdated versions of the files' contents.
"""  # noqa: E501

    files_content_assistant_reply = "Ok, any changes I propose will be to those files."

    files_no_full_files = "I am not sharing any files that you can edit yet."

    files_no_full_files_with_repo_map = """Don't try and edit any existing code without asking me to add the files to the chat!
Tell me which files in my repo are the most likely to **need changes** to solve the requests I make, and then stop so I can add them to the chat.
Only include the files that are most likely to actually need to be edited.
Don't include files that might contain relevant context, just files that will need to be changed.
"""  # noqa: E501

    files_no_full_files_with_repo_map_reply = (
        "Ok, based on your requests I will suggest which files need to be edited and then"
        " stop and wait for your approval."
    )

    repo_content_prefix = """Here are summaries of some files present in my git repository.
Do not propose changes to these files, treat them as *read-only*.
If you need to edit any of these files, ask me to *add them to the chat* first.
"""

    read_only_files_prefix = """Here are some READ ONLY files, provided for your reference.
Do not edit these files!
"""

    shell_cmd_prompt = ""
    shell_cmd_reminder = ""
    no_shell_cmd_prompt = ""
    no_shell_cmd_reminder = ""

    rename_with_shell = ""
    go_ahead_tip = ""
