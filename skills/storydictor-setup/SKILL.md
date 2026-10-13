---
name: storydictor-setup
description: Configure StoryDictor's local tools and audio access before the first full video story; never request or store secrets in a conversation.
---

# Set up StoryDictor

Read the user's requested language and goals and the bundled storydirector instructions. Confirm available browsing, image generation/inspection, local file tools, independent agents and audio perception. Explain real missing capabilities; do not invent them.

Find Python 3.10+, FFmpeg and ffprobe. Pillow is required for chapter overlays. Resolve executables on PATH or use user-provided paths. Configure a suitable TrueType font for the story language. Do not install paid services or alter accounts automatically.

Use storydirector/references/local-profile.json as a secret-free template. Save a separate production_profile.json inside each new episode. Configure the requested language and media paths. ElevenLabs credentials come from the configured environment variable or a user-managed secret file outside the project. Never ask the user to paste a key into chat or commit it to Git. The shipped voice/model IDs are empty: use available account APIs to choose actually supported values and permissions, then check included quota before production. The package does not include an account or subscription.

Record the host's current limitations. Independent script review and image QA require actual observations. Audio emotion QA requires actual listening; otherwise record the limitation. Keep delivery local and publishing manual unless separately authorized. Do not start paid production merely because installation has completed.

For setup details and examples read the repository README or README.ru.md. Preserve the user's existing installation and episode materials.
