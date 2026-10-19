#!/usr/bin/env python3
import argparse
import re
import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    print("Error: 'PyMuPDF' package is required. Install it via 'pip install PyMuPDF'.", file=sys.stderr)
    sys.exit(1)


def get_pdf_page_count(pdf_path: Path) -> int | None:
    """Extract page count using PyMuPDF safely."""
    try:
        with fitz.open(pdf_path) as doc:
            return len(doc)
    except Exception as err:
        print(f"[ERROR] Unreadable PDF '{pdf_path}': {err}", file=sys.stderr)
        return None


def update_markdown_pagecount(md_path: Path, table_content: str) -> None:
    """
    Inserts or updates the '# Page Count\n---\n...\n---' block in the Markdown file.
    Prepends to the top of the file if the target block is missing.
    """
    block_text = f"# Page Count\n---\n\n{table_content}\n\n---"
    pattern = re.compile(r"# Page Count\s*\n---\n\n.*?\n\n---", re.DOTALL)

    if md_path.exists():
        try:
            content = md_path.read_text(encoding="utf-8")
        except Exception as err:
            print(f"[ERROR] Failed reading Markdown file '{md_path}': {err}", file=sys.stderr)
            return

        if pattern.search(content):
            updated_content = pattern.sub(block_text, content, count=1)
        else:
            updated_content = f"{block_text}\n\n{content.lstrip()}"
    else:
        updated_content = f"{block_text}\n"

    try:
        md_path.write_text(updated_content, encoding="utf-8")
        print(f"[SUCCESS] Updated '{md_path}'")
    except Exception as err:
        print(f"[ERROR] Failed writing to '{md_path}': {err}", file=sys.stderr)


def format_folder_table(
    child_dirs: list[tuple[str, int]], 
    child_files: list[tuple[str, int]], 
    total_pages: int
) -> str:
    """Formats a Markdown table listing child items and the aggregate page count."""
    lines = [
        "| Name | Type | Page Count |",
        "| :--- | :--- | :--- |"
    ]
    for folder_name, count in sorted(child_dirs, key=lambda x: x[0].lower()):
        lines.append(f"| {folder_name} | Folder | {count} |")
    for file_name, count in sorted(child_files, key=lambda x: x[0].lower()):
        lines.append(f"| {file_name} | File | {count} |")
    lines.append(f"| **Total** | | **{total_pages}** |")
    return "\n".join(lines)


def process_directory(target_dir: Path) -> None:
    """Recursively processes PDFs and folders bottom-up to aggregate page counts."""
    target_dir = target_dir.resolve()
    if not target_dir.is_dir():
        print(f"[FATAL] Directory path does not exist or is not a folder: '{target_dir}'", file=sys.stderr)
        sys.exit(1)

    # Collect all directories and sort by depth descending (bottom-up traversal)
    all_dirs = [target_dir] + [p for p in target_dir.rglob("*") if p.is_dir()]
    all_dirs.sort(key=lambda p: len(p.parts), reverse=True)

    folder_page_counts: dict[Path, int] = {}

    for current_dir in all_dirs:
        # Get PDF page counts in current directory
        pdf_files = [
            p for p in current_dir.iterdir()
            if p.is_file() and p.suffix.lower() == ".pdf"
        ]

        child_file_entries: list[tuple[str, int]] = []
        direct_pdf_sum = 0

        for pdf_path in pdf_files:
            pages = get_pdf_page_count(pdf_path)
            if pages is not None:
                child_file_entries.append((pdf_path.name, pages))
                direct_pdf_sum += pages

        # Get subfolder page counts
        child_dir_entries: list[tuple[str, int]] = []
        subdirs_sum = 0

        subdirs = [p for p in current_dir.iterdir() if p.is_dir()]
        for sub_path in subdirs:
            sub_count = folder_page_counts.get(sub_path, 0)
            if sub_count > 0:
                child_dir_entries.append((sub_path.name, sub_count))
                subdirs_sum += sub_count

        total_folder_pages = direct_pdf_sum + subdirs_sum
        folder_page_counts[current_dir] = total_folder_pages

        # Generate {folder_name}.md inside the current directory
        if child_file_entries or child_dir_entries:
            table_str = format_folder_table(child_dir_entries, child_file_entries, total_folder_pages)
            folder_md_path = current_dir / f"{current_dir.name}.md"
            update_markdown_pagecount(folder_md_path, table_str)


def main():
    parser = argparse.ArgumentParser(
        description="Recursively count PDF pages and summarize folder totals inside each directory."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=Path("."),
        type=Path,
        help="Path to root target directory (default: current directory)"
    )

    args = parser.parse_args()
    process_directory(args.directory)


if __name__ == "__main__":
    main()