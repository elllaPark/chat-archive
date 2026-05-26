# Chat Archive

A local desktop app for archiving exported KakaoTalk chat data.

The goal of this project is to move old KakaoTalk chat history, photos, and videos out of the phone app while keeping the conversation readable in a familiar chatroom-style interface. The current app parses KakaoTalk `.txt` exports into JSON and displays the messages in an Electron UI.

## Current Status

This is an early-stage personal archive app.

Working pieces:

- Electron desktop app shell
- KakaoTalk-style chat UI
- Native folder picker for adding an archive
- Python parser for KakaoTalk `.txt` exports
- Local cache of parsed messages under Electron `userData`
- Basic time-of-day and season metadata on parsed messages

Not finished yet:

- Reliable drag-and-drop folder import
- Photo/video file matching and rendering
- Full search
- Analysis dashboard
- Automated tests

## Project Structure

```text
src/
  electron/
    main.js        # Electron main process, IPC, archive indexing
    preload.js     # Safe APIs exposed to the renderer
  parser/
    chatParse.py   # KakaoTalk text parser
    main.py        # CLI wrapper for the parser
  renderer/
    index.html     # Main app UI
    handleArchive.js
    dropDownSearch.js
    chat.css
    style.css
    img/           # UI icons
local-data/        # Ignored local/private imports and generated files
```

## Requirements

- Node.js
- npm
- Python 3

## Setup

Install dependencies:

```bash
npm install
```

If Electron fails to launch because its app bundle is missing, rebuild Electron:

```bash
npm rebuild electron
```

## Run The App

```bash
npm start
```

## Run The Parser Manually

```bash
python3 src/parser/main.py <input.txt> <output.json>
```

Example:

```bash
python3 src/parser/main.py local-data/imports/chat.txt /private/tmp/chat-output.json
```

## Parsed Message Format

The parser currently outputs messages like:

```json
{
  "timestamp": "2024-06-09 22:04",
  "sender": "mmm",
  "content": "message text",
  "time_of_day": "night",
  "season": "summer"
}
```

## Privacy Notes

This project is designed for private chat history and media.

Do not commit:

- KakaoTalk `.txt` exports
- Photos or videos
- Generated parser JSON
- Electron archive cache files
- Anything in `local-data/`

The `.gitignore` is set up to keep local/private archive data out of Git.

## Development Notes

Archive import flow:

1. The renderer calls `window.archivesAPI.add()` or `window.archivesAPI.addByPath(path)`.
2. Electron indexes selected `.txt` files.
3. Electron runs the Python parser.
4. Parsed messages are merged, sorted, and cached.
5. The renderer reads the cache and displays chat bubbles.

Known technical issues:

- Drag-and-drop currently relies on renderer-side file path behavior that may not work in modern Electron.
- Archive indexing only scans direct child `.txt` files.
- Media messages such as `Photo` and `Video` are not linked to actual files yet.
- Some multiline messages may not be preserved.

## License

ISC
