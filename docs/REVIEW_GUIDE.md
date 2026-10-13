# Review guide

This is a skills-only desktop plugin. It has no MCP server, hosted endpoint or reviewer account. Full media production requires tools provided by the host and the reviewer's own configured services. The following cases can be tested without paid generation.

## Script review

Prompt: “Review this original life-ladder story. Do not generate media yet.”

Expected: a frozen full text, separate independent critic and fresh reader, exact quotations, current SHA256 evidence and a reasoned revision decision. The reader receives an audience persona, not author explanations. Missing independent agents is recorded as limited_review, not a fabricated pass.

## Stale editorial evidence

Change a meaningful phrase after a passing synthetic review and run editorial_guard.py against story.json. Expected: needs_agent/story_editorial before new dependent requests. Existing completed cache remains recoverable; an uncertain paid request is not blindly repeated.

## Visual-lore planning

Prompt: “Plan an episode in a fictional historical city using provided reference sources; no images yet.”

Expected: explicit source references and a world/character bible, scene-specific costumes, places and props, and review criteria. A genre label alone is not treated as verified visual lore.

## Image queue reliability

Run the synthetic image_queue tests. Expected: no duplicate claims, bounded revisions, checked dependency hashes, and explicit reconciliation of uncertain outcomes. The queue does not call a generation API.

## Emotional narration direction

Prompt: “Direct these three paragraphs for restrained fear, fatigue and relief; do not call TTS.”

Expected: different intentions/tempo/pauses in separate direction notes, clean spoken text, no claim that STT proves emotions, and no unsupported voice tags invented.

## Boundaries

Without configured credentials or quota, do not start paid production. Do not publish an episode merely because it is complete. Do not transfer world characters or private user material across episodes. A beautiful image alone is insufficient lore QA.

Technical tests are synthetic evidence. They are not a claim that a new host installation has produced or perceptually reviewed a complete movie.
