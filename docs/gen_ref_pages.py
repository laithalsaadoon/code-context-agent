"""Generate API reference pages from source code.

This script is run by mkdocs-gen-files during the documentation build.
It auto-generates markdown files for each Python module in both workspace
packages: ``strands_teams`` (the framework) and ``code_context_agent`` (the
codebase-analysis use case).
"""

from pathlib import Path

import mkdocs_gen_files

nav = mkdocs_gen_files.Nav()

root = Path(__file__).parent.parent

# (import package name, source root) for each workspace member.
packages = [
    ("strands_teams", root / "packages" / "strands-teams" / "src" / "strands_teams"),
    ("code_context_agent", root / "packages" / "code-context-agent" / "src" / "code_context_agent"),
]

for module, src in packages:
    for path in sorted(src.rglob("*.py")):
        module_path = path.relative_to(src).with_suffix("")
        doc_path = Path(module, path.relative_to(src).with_suffix(".md"))
        full_doc_path = Path("reference", doc_path)

        parts = tuple(module_path.parts)

        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
            doc_path = doc_path.with_name("index.md")
            full_doc_path = full_doc_path.with_name("index.md")
            nav_parts = (module,) if not parts else (module, *parts)
        elif parts and parts[-1] == "__main__":
            continue
        else:
            nav_parts = (module, *parts)

        nav[nav_parts] = doc_path.as_posix()

        with mkdocs_gen_files.open(full_doc_path, "w") as fd:
            identifier = f"{module}.{'.'.join(parts)}" if parts else module
            fd.write(f"::: {identifier}\n")

        mkdocs_gen_files.set_edit_path(full_doc_path, path.relative_to(root))

with mkdocs_gen_files.open("reference/SUMMARY.md", "w") as nav_file:
    nav_file.writelines(nav.build_literate_nav())
