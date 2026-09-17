One correction to the sentence that closes the thread's options — @shawnacason, 2026-08-27: "it cannot see whether the read actually happened … the thing none of us can build."

After the fact it is buildable today, because Claude Code already writes the record. Transcripts carry `attachment.type == "instructions"` with `files[{path,type,content}]`, and `attachment.type == "prompt_snapshot"` with `systemPrompt[]` — the rendered context, not a reconstruction of where the cut landed. Comparing an artifact's body against those blobs — whole-body exact, then per-section with `MEMORY.md` line by line, then a 200-character LCS for reflowed text — gives per session and per artifact: `loaded_complete`, `loaded_truncated`, `not_loaded`, `missing_on_disk`, `unknown`. Every claim carries a `<transcript file>:<line>` you can check with `sed -n`.

The caveat first, since this thread re-runs numbers. `unknown` is load-bearing and is the honest answer for most sessions: many carry only a couple of `prompt_snapshot` attachments, and calling those `not_loaded` would be a lie. It proves only that the text was in the rendered context, not that the model attended to it. It does not close proposal 1 — the running session still cannot know.

Stdlib only; it never writes to the Claude home it reads. One file, nothing installed:

```
curl -LO https://github.com/Linxiushen/precedent/releases/download/v0.1.0-rc1/precedent.pyz
python3 precedent.pyz audit --share
```

The card is counts-only: no quotes, paths, project names or session ids — useful if you are posting from a work machine. Source: github.com/Linxiushen/precedent (Apache-2.0, pre-release, one machine).

AI-assistance disclosure: drafted with Claude Code under my authorization; the limits stated are the package's own.
