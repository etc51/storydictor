# Services and credentials / Сервисы и ключи

StoryDictor is an agent-led desktop workflow. It has no hosted service, telemetry endpoint or bundled account credentials.

- Research: use available browsing tools and primary sources. Save evidence inside the episode.
- Images: use the host's built-in image generation and inspection tools. A separate image API is not configured. Do not switch to a paid provider without explicit authorization.
- Voice: ElevenLabs API, configured through `ELEVENLABS_API_KEY`. Choose a voice and supported model after checking account access. Never copy keys into scripts, prompts, Git commits or episode manifests.
- Alignment: supported timestamps, speech recognition or forced alignment. Verify text against the actual audio.
- Music/SFX: a configured service or properly licensed library. Check rights for the intended use. No user's previous license confirmation transfers to another installation.
- Render: local Python, FFmpeg and ffprobe; Pillow for chapter overlays. Configure executable paths in the episode profile.

No overage, PAYG, new subscription or publishing action is authorized merely by starting a story. Verify the account's current included quota before paid operations, reserve capacity across concurrent episodes, and reconcile uncertain outcomes before retrying.

Emotion direction belongs in separate notes and only supported TTS controls. Listen to key transitions when audio perception is available. STT and file decoding verify content/technical health, not acting quality. Record any missing perception capability honestly.

Русский: настройте собственный ключ ElevenLabs, голос, модель, FFmpeg и условия использования музыки. Чужие права, квоты и подписки не наследуются. Ключи держите в переменных среды или менеджере секретов, вне проектов и Git. Доплаты и публикация требуют отдельного поручения.
