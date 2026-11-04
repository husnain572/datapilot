"""A quick check of generated code before it's sent to the sandbox.

This is an early warning that catches mistakes and saves a sandbox run. It is NOT a
security boundary, because determined code can get around checks like these. The
sandbox is what actually contains the code.
"""

import ast

ALLOWED_IMPORTS = {
    "calendar",
    "collections",
    "datetime",
    "decimal",
    "functools",
    "itertools",
    "math",
    "numpy",
    "pandas",
    "re",
    "statistics",
}
BLOCKED_NAMES = {
    "__import__",
    "breakpoint",
    "compile",
    "delattr",
    "eval",
    "exec",
    "getattr",
    "globals",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
}
# The data is already loaded as `df`, and results go through emit_* functions.
BLOCKED_ATTRIBUTES = {
    "read_clipboard",
    "read_csv",
    "read_excel",
    "read_feather",
    "read_fwf",
    "read_hdf",
    "read_html",
    "read_json",
    "read_parquet",
    "read_pickle",
    "read_sql",
    "read_table",
    "read_xml",
    "to_clipboard",
    "to_csv",
    "to_excel",
    "to_feather",
    "to_hdf",
    "to_html",
    "to_json",
    "to_parquet",
    "to_pickle",
    "to_sql",
    "to_xml",
}


def find_problems(code: str) -> list[str]:
    """Return what's wrong with the code, or an empty list if nothing was found."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError) as error:
        return [f"The code has a syntax error: {error}"]

    problems: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        else:
            modules = []
        for module in modules:
            if module.split(".")[0] not in ALLOWED_IMPORTS:
                problems.add(f"Importing '{module}' isn't allowed.")

        if isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            problems.add(f"Using '{node.id}' isn't allowed.")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr in BLOCKED_ATTRIBUTES:
                problems.add(f"Using '.{node.attr}' isn't allowed.")
    return sorted(problems)
