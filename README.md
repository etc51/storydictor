<p align="center"><img src="assets/icon.png" width="180" alt="StoryDictor film-frame staircase icon" /></p>

# StoryDictor

**Turn a topic into an original narrated video story, with independent editorial review, expressive voice direction and lore-checked images.**

[Русский](README.ru.md) · [Directory description](docs/catalog-description.en.md) · [Licenses](THIRD_PARTY_NOTICES.md)

StoryDictor is a skills plugin for local ChatGPT/Codex work. Its main skill is `storydirector`; bulk image production is `story-images` (“сторикартинки”). The package contains creative workflow instructions, local production helpers and selected editorial methods. An active agent performs writing, generation and artistic review while Python supports the queue, voice requests, alignment, sound and rendering.

It is not a hosted service or an unattended movie generator. Complete production requires the host's tools and a configured desktop environment. No account, subscription, credentials or finished films are included.

## Capabilities

- Research a topic, world, era and canon constraints before outlining.
- Develop an original personal life, character motives, relationships, causal turns and an earned ending.
- Use the optional **life-ladder story** format: one continuous life in which decisions expand capabilities, influence and responsibility.
- Review the entire script with an independent critic and a fresh reader before costly dependent stages.
- Maintain an episode-specific character bible, age/outfit versions, environments and visual references.
- Direct narration through intention, emotion changes, tempo, emphasis and meaningful pauses.
- Align subtitles to real speech and compare transcription with the source text.
- Coordinate separate image jobs through a transactional queue, independent workers and visual QA.
- Check visual lore: landscapes, buildings, interiors, clothing, armor, weapons, vehicles, technology, symbols and props.
- Render local camera/light motion, transitions and parallax when genuine separate layers are supplied. Character animation or an external video API is not enabled automatically.
- Match music and sound effects to events, record provenance and assemble a reviewed H.264/AAC MP4, cover and description package.

## Default episode specification

| Setting | Default |
|---|---|
| Duration | 25–35 minutes |
| Story images | 150–200 distinct, accepted images |
| Tense scenes | Image changes every 4–8 seconds |
| Ordinary narration | 10–15 seconds |
| Selected emotional pauses | 20–25 seconds |
| Video | 16:9, 1920×1080, 24 fps |
| Narration | One expressive storyteller |
| Motion | Camera, light and transitions; parallax with separate layers |

References, covers, rejected versions, corrections and crops do not count as new story images. Shots follow events and actual speech rather than uniform time division. The default workflow uses appropriate in-world visual humor or object lettering in half of the story images; heavy dramatic scenes should retain their emotional purpose. Explicit user requirements take precedence.

## Independent editorial workflow

`writer → independent critic → fresh reader → revision → current-version review`

The critic receives a frozen full script, requirements and world sources. A different fresh agent receives the same full script and an audience persona, without the bible, author explanations or critic's report. Findings cite exact passages and bind to the script's SHA256.

The coordinator addresses weak motives, broken causality, repetitive events, loss of tension and unearned payoffs while preserving strong scenes. Two ordinary revision cycles prevent endless polishing; persistent substantial problems require an outline rethink or an honest `needs_revision` status.

`editorial_guard.py` checks report provenance, hashes, scope, quotations and findings resolution. It does not judge literary taste. A writer cannot dismiss a blocking finding with their own `resolved` label. Changed text requires a new full review. Reader simulation is subjective, not an audience survey or retention prediction.

## Expressive narration

Direction notes remain separate from spoken text. Select a genuinely available voice/model and use only their supported controls. Fear, exhaustion, pride and relief should not receive the same delivery.

Check omissions, repetitions, pronunciation, accidentally spoken tags, noise, clipping and key emotional transitions. Speech recognition and decoding verify content and technical health, not acting quality. If audio perception is unavailable, record that limitation instead of claiming emotion QA passed.

## Installation

After directory approval and publication, install StoryDictor from the shared ChatGPT/Codex plugin directory. A GitHub release alone does not establish directory approval.

For repository distribution in a supported local client:

```bash
codex plugin marketplace add etc51/storydictor --ref main
```

Open the desktop app's Plugins directory, select the StoryDictor source and install the plugin. Refresh or restart the app if required, after completing active work. This repository includes `.agents/plugins/marketplace.json`, a portable root `plugin.json` and a `.codex-plugin/plugin.json` compatibility manifest.

Alternatively, copy all directories under `skills/` into your client's skill directory. Copying only `storydirector` omits its bundled workflow dependencies.

## Local setup

1. The host must provide browsing, image generation/inspection, local file access and independent agents. The skill cannot create missing host capabilities.
2. Install Python 3.10+, FFmpeg, ffprobe and curl. Pillow and a suitable TrueType font are needed for chapter overlays. Set `tools.curl` if curl needs a custom path.
3. Run `storydictor-setup`. The secret-free template is `skills/storydirector/references/local-profile.json`.
4. Configure ElevenLabs through `ELEVENLABS_API_KEY` using your environment or secret manager. Never paste a key into a conversation or commit it.
5. Choose actually supported voice/model IDs and verify account access. IDs in the template are empty; another installation's quota and permissions do not transfer.
6. Keep an episode-specific `production_profile.json` with language, executable/font paths, audio access and material rights.

```bash
python -m pip install -r requirements.txt
python skills/storydirector/scripts/pipeline.py init --project output/my-story --topic "A life on a dangerous road"
```

The agent prepares `story.json`, the bible, editorial reports and consistent production inputs. Then:

```bash
python skills/storydirector/scripts/editorial_guard.py --project output/my-story --story output/my-story/story.json
python skills/storydirector/scripts/pipeline.py run --project output/my-story
python skills/storydirector/scripts/pipeline.py status --project output/my-story
```

`needs_agent` is work for the active agent, not an automatic request for user approval. The CLI does not write scripts or call the host's built-in image generator by itself.

## Image queue and recovery

The bundled SQLite queue tracks permanent IDs, revisions, dependencies, hashes, leases, bounded retries and uncertain outcomes. Concurrency follows actual host/tool slots: planning 150 images does not imply 150 simultaneous calls.

An uncertain outcome must be reconciled before retrying. Bible changes create new input revisions and invalidate affected dependents. Only the coordinator updates shared manifests and QA. Keep active, useful materials; deleting replaced files requires explicit authorization and dependency checks.

After script edits, update affected voice, alignment, shots, images and render outputs. Preserve valid independent results. See `skills/story-images/references/parallel-production.md` for the detailed protocol.

## Costs, rights and delivery

Complete production may consume your own ElevenLabs quota. Check current included capacity before new paid requests and account for concurrent episodes. The package does not enable overage, PAYG, new subscriptions or a paid image provider for speed. Verify the rights of music, voices, world references and other materials for the intended release.

Delivery includes a reviewed MP4, separate cover, SRT, title, description and reproducible project. Configure your own community URL and platforms. Publishing to YouTube, Rutube, VK Video or another service is a separate instruction.

## Development checks

```bash
python -m unittest discover -s skills/storydirector/scripts -p "test_*.py" -v
python skills/story-images/scripts/test_image_queue.py
```

Tests use temporary synthetic data without paid generation. They validate persistence, hashes, queues and operational constraints, not story popularity or voice acting. See `docs/REVIEW_GUIDE.md` for package review scenarios.

## Licensing and provenance

Original orchestration and production helpers: MIT. Bundled editorial skills retain their own MIT or Apache-2.0 licenses. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and the source manifest. The icon was created with built-in image generation. References to services or games do not imply endorsement.

No real episode scripts, audio, story images, personal account settings or credentials are included.
