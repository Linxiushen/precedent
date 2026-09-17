One correction to the sentence that closes the thread's options — @shawnacason, 2026-08-27: "it cannot see whether the read actually happened … the thing none of us can build."

After the fact it is buildable today, because Claude Code already writes the record. Transcripts carry `attachment.type == "instructions"` with `files[{path,type,content}]`, and `attachment.type == "prompt_snapshot"` with `systemPrompt[]` — the rendered context, not a reconstruction of where the cut landed. Comparing an artifact's body against those blobs — whole-body exact, then per-section with `MEMORY.md` line by line, then a 200-character LCS for reflowed text — gives per session and per artifact: `loaded_complete`, `loaded_truncated`, `not_loaded`, `missing_on_disk`, `unknown`. Every claim carries a `<transcript file>:<line>` you can check with `sed -n`.

The caveat first, since this thread re-runs numbers. `unknown` is load-bearing and is the honest answer for most sessions: many carry only a couple of `prompt_snapshot` attachments, and calling those `not_loaded` would be a lie. It proves only that the text was in the rendered context, not that the model attended to it. It does not close proposal 1 — the running session still cannot know.

Stdlib only; it never writes to the Claude home it reads.

Correction to my own comment: I originally linked a downloadable build here. That repository is not public, so I have removed the link rather than leave a dead one pointing at a 404. Apologies to anyone who clicked it in the last two hours.

Nothing above depends on the link. The method is the whole contribution — the two attachment types, the three-stage comparison (whole-body exact, then per-section, then a 200-character LCS for reflowed text), and the five states with `unknown` load-bearing. All of it is reproducible from the transcript format on your own disk.

AI-assistance disclosure: drafted with Claude Code under my authorization; the limits stated are the package's own.
