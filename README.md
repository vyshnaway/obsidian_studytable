# Obsidian Learning Vault

This repository is a general-purpose Obsidian vault for learning, research, reference notes, and ongoing projects. Use it as a quiet workbench: open one topic, learn or work on it, capture what matters, and stop when the session is done. The existing folders include subject-specific collections, but the workflow applies to anything you want to understand or keep track of.

## Start Here

1. Open Obsidian and choose **Open folder as vault**, then select this repository folder.
2. Start from the note, folder, index, or source relevant to what you want to work on.
3. Pick one note, source, or task to focus on. Use Obsidian's Quick Switcher (`Ctrl+O`) to open a note by name without browsing around.
4. Keep only that topic and its source material open. Hide sidebars you do not need, and avoid opening Graph view, browsing plugins, or reorganizing folders during a study session.

## A Simple Study Session

1. **Choose one outcome.** For example: understand a concept, make a decision, finish a small task, or solve a problem.
2. **Work from a useful source.** Read or explore the relevant material. Mark only ideas that are unclear or important; do not try to transcribe everything.
3. **Capture what you learned.** In the relevant note, write a concise explanation in your own words, the key evidence or steps, and an example, decision, or result.
4. **Check and refine.** Compare your understanding with the source or outcome. Correct errors and record specific open questions rather than copying more material.
5. **Close with a next step.** Add one brief follow-up or review prompt, then finish. Use a note in `Journals/` only when a dated session log is useful; it is optional.

Prefer a few durable notes over a separate note for every passing thought. Use `[[Wiki links]]` when a connection will help you find or understand something later, and link to an existing note instead of duplicating its content.

## Where Things Go

- Create folders for each semester and subfolders for each folders within. Oraganize related resources within corresponding folders.
- `Journals/` is configured as the Daily Notes folder. Use it for dated study logs, not as a replacement for topic notes.
- `Attachments/` is for supporting files that are not managed alongside a particular source.
- `Annotations/` contains images referenced by ZotFlow annotation notes. Avoid renaming or moving these files unless you also update their links.
- `ZotFlow/Local/` contains ZotFlow's local PDF reader/source notes. Treat notes with `zotflow-locked: true` as plugin-managed; add durable explanations to the relevant topic note instead.
- `Scripts/` contains repository scripts. Leave `.obsidian/` settings alone unless you intend to change the vault setup.

## Keep the Vault Quiet

- Start from one topic or task and one concrete outcome; do not begin by browsing the whole vault.
- Keep source annotations short. Put explanations, reasoning, mistakes, decisions, and review prompts in your own notes.
- Do not force a graph, tag system, elaborate template, or daily streak. Add organization only when it solves a real retrieval problem.
- When work exposes a gap, write down the exact question, misconception, or unresolved decision and what resolved it. Revisit that prompt later without rereading everything.
- End a session by leaving the next action obvious, then close the vault or move on with your day.

## Backups

This folder is an Obsidian vault and can also be opened from its local/cloud-synced location. The current `.gitignore` ignores most vault content by default, so do not assume that notes, PDFs, or attachments are included in Git backups. Review `.gitignore` and check Git's status before relying on Git for version history; make sure your chosen backup also covers the files you care about.
