"""
Recursive Markdown and README Generator for Directory Trees.

This script recursively traverses a specified directory to:
1. Ensure a Folder note exists in every non-hidden folder.
2. Generate an empty Markdown (.md) file matching the name of each non-hidden
   document file (PDF, DOC, DOCX, etc.).

Key Features:
- Accepts a folder path via CLI argument (defaults to current working directory).
- Ignores hidden files and hidden parent directories (e.g., .git/ or .cache/).
- Preserves existing .md and Folder note files to prevent overwriting.
- Targets common standard document formats.
"""

import argparse
from pathlib import Path

# File extensions to target
TARGET_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".rtf",
    ".odt",
    ".txt",
    ".epub",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
}


def is_hidden(path: Path) -> bool:
    """Check if a path or any of its directory components start with a dot."""
    return any(part.startswith(".") for part in path.parts)


def generate_markdown_files(target_directory: str | Path) -> None:
    """
    Recursively scan target_directory to create Folder note files in directories
    and corresponding .md files for non-hidden document files.
    """
    root_dir = Path(target_directory).resolve()

    if not root_dir.exists() or not root_dir.is_dir():
        print(f"Error: Directory '{root_dir}' does not exist or is not a directory.")
        return

    print(f"Scanning target directory: {root_dir}\n")

    foldernote_created = 0
    foldernote_skipped = 0
    doc_md_created = 0
    doc_md_skipped = 0

    # 1. Process directories for Folder note generation
    for current_dir, dirs, _ in root_dir.walk():
        rel_dir = current_dir.relative_to(root_dir)

        # Skip hidden directories and prune them from further traversal
        if rel_dir != Path(".") and is_hidden(rel_dir):
            dirs.clear()
            continue

        # Prune hidden subdirectories so walk() won't enter them
        dirs[:] = [d for d in dirs if not d.startswith(".")]

        foldernote_path = current_dir / (current_dir.name + ".md")
        if foldernote_path.exists():
            foldernote_skipped += 1
        else:
            try:
                foldernote_path.touch()
                print(f"Created Folder note: {foldernote_path}")
                foldernote_created += 1
            except Exception as e:
                print(f"Failed to create {foldernote_path}: {e}")

    # 2. Process document files for corresponding .md creation
    for file_path in root_dir.rglob("*"):
        if not file_path.is_file():
            continue

        rel_file = file_path.relative_to(root_dir)

        # Skip hidden files or files inside hidden folders
        if is_hidden(rel_file):
            continue

        if file_path.suffix.lower() in TARGET_EXTENSIONS:
            md_path = file_path.with_suffix(".md")

            if md_path.exists():
                doc_md_skipped += 1
                continue

            try:
                md_path.touch()
                print(f"Created Doc MD: {md_path}")
                doc_md_created += 1
            except Exception as e:
                print(f"Failed to create {md_path}: {e}")

    print("\nProcess finished.")
    print(f"Folder note created: {foldernote_created} | skipped: {foldernote_skipped}")
    print(f"Document .md created: {doc_md_created} | skipped: {doc_md_skipped}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Recursively generate Folder notes in folders and .md files for target documents."
    )
    parser.add_argument(
        "folder_path",
        nargs="?",
        default=".",
        help="Target folder path (default: current working directory)",
    )

    args = parser.parse_args()
    generate_markdown_files(args.folder_path)