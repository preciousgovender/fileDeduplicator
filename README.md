# File Deduplicator

A local file deduplicator with a Tkinter GUI. Scans a folder tree, finds identical files using BLAKE2b hashing, and shows side-by-side previews so you can review before deleting.

## Features

- **Two-phase scanning** — groups files by size first, then hashes only the size collisions, so it stays fast on large trees
- **Side-by-side preview** — text files render as text, binaries as a hex dump
- **Pick your keeper** — choose which file in each group to keep; the rest get deleted
- **Bulk delete** — nuke all duplicates at once, keeping the first (sorted by path) in each group
- **Threaded scanning** — UI stays responsive, cancel anytime
- **Hardlink-aware** — skips files with the same (dev, inode) so hardlinks aren't flagged as dupes
- **Skips noise** — ignores .git, node_modules, __pycache__

## Requirements

- Python 3.10+
- Tkinter (bundled with the standard python.org installer on Windows/macOS)
- No external dependencies

On Debian/Ubuntu, Tkinter may need to be installed separately:

    sudo apt install python3-tk

## Run

    python main.py

1. Click Add Folder… and pick a directory (repeat for multiple roots)
2. Click Scan
3. Click a group on the left to see its files
4. Pick the file to keep, then Delete duplicates in selected group
   — or use Delete ALL duplicates to clear everything at once

## How it works

1. Walk each root directory, recording (device, inode) to avoid double-counting hardlinks
2. Group files by size — a duplicate must match on size, so this cheaply eliminates most files
3. Hash only the files that share a size, using BLAKE2b (fast, cryptographic-strength, in stdlib)
4. Regroup by hash; any group with 2+ files is a real duplicate set

## Project layout

    deDupe/
    ├── main.py       # Tkinter UI
    ├── scanner.py    # Walk + hash engine (threaded, cancellable)
    └── README.md

## Safety notes

- Deletion is permanent — files are unlinked, not moved to trash
- Preview before you delete; the Open and Reveal buttons let you inspect files in their native apps
- There is no undo

## Ideas / TODO

- [ ] Move to trash instead of permanent delete (send2trash)
- [ ] Perceptual hashing for near-duplicate images (pHash/dHash)
- [ ] Cache scan results to JSON for instant re-scans
- [ ] Export group list to CSV
- [ ] Path whitelist (never delete from protected folders)
- [ ] Minimum-size filter in the UI

## License

MIT
