# Private cloud demo

The prepared configuration runs one Ubuntu x86_64 VM, including a Tencent Cloud Hong Kong VM:

`Browser → Caddy HTTPS + shared password → one FastAPI process → SQLite + original audio volume`

`FastAPI → local SenseVoiceSmall INT8 + Silero VAD + Sherpa-ONNX diarization → official DeepSeek API for text/memory JSON`

Speech inference runs on the app host: audio stays on the local device for a local app and is uploaded to the chosen VM for a hosted app. Speech does not require an external ASR account/key. Transcript text and relevant memories still go directly to the official DeepSeek API by default, so this is not an entirely offline system. A2Agent is a third-party gateway and only receives those texts if explicitly configured instead of the official endpoint.

Everyone using the shared login can see and change the same recordings, memories, datasets and settings. Separate user accounts and data ownership are not implemented.

SenseVoiceSmall INT8 is deployed on the existing **4-vCPU/8-GB CPU VM**. It avoids the previous Torch/CUDA/large-model runtime; the official INT8 weight file is about 228 MB. The bounded host benchmark below measures the complete VAD/transcription/diarization path; it does not establish sustained capacity or real-conversation accuracy. DeepSeek usage is billed separately from hosting. The superseded Qwen/GPU downloads have been stopped; Qwen remains optional unconfigured code.

## Current verification boundary

The dated verification records below describe successive releases. The current upload-flow policy supersedes their manual recording-date editor and interpretation-approval requirement: server upload time is the canonical date, text processing follows transcription automatically, topics are numbered and editable, and proactive support does not require an interpretation-review click. Speaker confirmation and the existing permission/condition checks still apply.

### Real Warning release

Warning now uses the user-confirmed words in real recordings. Its single preference controls actual check-ins independently of the legacy fictional-demo flag. A real candidate requires strong explicit distress today, or the same emotional/social distress topic on at least three distinct days in the last seven, including today. These are transparent application rules, not clinical thresholds or diagnoses. The bounded English/Chinese recogniser ignores reported/quoted speech, negation, hypothetical or withdrawn statements, and explicit improvement within the source. It does not infer distress from voice characteristics or a generated paraphrase. Unrecognised wording produces no warning.

Every candidate carries the exact current evidence, dates and source versions in `warning_trigger`, with `support_basis=acute_distress|repeated_distress`. Real Warning has priority in automatic type selection; source confirmation, user corrections, retention, preferences, scheduling, quiet mode, cooldown, feedback and final source-version checks remain in effect. Cancelling a delivered card retains its automatic receipt so a correction cannot accidentally erase the cooldown. DeepSeek renders a supportive check-in using the evidence language; model output cannot change source conditions or type. Legacy fictional examples remain clearly marked and require their separate demo permission.

Verification uses invented normal-source English/Chinese text in disposable databases, never marks real user data as fictional, and does not seed the live workspace. `scripts/verify_real_warning.py --live-model` exercises actual configured DeepSeek rendering; the normal-upload/identity-confirmation/API flow has separate model-double tests. Warning remains an in-app check-in, evaluated while the application is active; OS push notifications and medical triage are not implemented.

The final real Warning release is deployed from `artifacts/cloud/hear-you-are-real-warning-20261018-r2.tar.gz`: **113 files, 318,521 archive bytes**, SHA-256 `55566ebea69928dc5fc2e20c166af4292b5183bea8f7efb28786628de128be3b`. Secret scanning passed; no credentials, app data or model weights were packaged. This exact archive remains unchanged by later verification-document edits. Assets: `index-CxeRnhAN.js` / `index-8SIHFkCt.css`. Local verification passed **858 backend tests, 65 frontend helpers, production build and pip check**. The final recogniser also handles smart-apostrophe negation. The cloud candidate passed the synthetic upload/edit/scheduling smoke and three actual DeepSeek English/Chinese/repeated-distress scenarios in disposable databases. The running container separately passed positive/negated Warning assertions after rollout.

The app is healthy, authenticated HTTPS/assets passed, unauthenticated access remains HTTP 401, and the original one recording, 16 memories, raw audio, transcript, topics, speaker identity and all preferences were preserved. Warning was already on and remains on. Current user evidence does not match either real Warning rule; no demonstration source or alert was inserted. The existing requested Comfort remains available. Source/stopped-data backups are retained under `/root/hear-you-are-real-warning-20261018-r2/`, with previous image `hear-you-are-app:before-real-warning-20261018-r2`. Safe reports: `artifacts/cloud-deploy/real-warning-tests-20261018.json`, `real-warning-live-verification-20261018.json`. Browser proof: `real-warning-live-20261018.jpg`; server proof: `real-warning-server-verified-20261018.jpg`.

### Warning preference control (superseded by real Warning)

The missing fourth preference is deployed as one accessible **Warning (fictional demo)** switch in **Preferences & model**. It atomically updates `preferences.warning` and `warning_demo_enabled`, displays off for mismatched legacy flags, and keeps the other preferences unchanged. Defaults and the requirement for explicitly fictional sources remain unchanged.

Source: `artifacts/cloud/hear-you-are-warning-pref-20261018.tar.gz`, 107 files / 297,848 archive bytes, SHA-256 `8610ec1eca089cd635ce0d0b1220006064b39b22c8c4471a0d9a0b1ba5b4d4a4`. The exact archive remains unchanged by this later verification record. Assets: `index-BCHYnVcR.js` / `index-8SIHFkCt.css`. The build and 65 frontend helpers passed, plus eight focused existing Warning backend cases and an isolated atomic rollback/source/schedule preservation check. Backend behavior did not change.

After the user completed Tencent identity verification, the candidate passed the disposable upload/display-scheduling smoke and the cloud app restarted healthy. Previous source/stopped-data backups are retained under `/root/hear-you-are-warning-pref-20261018`, with image `hear-you-are-app:before-warning-pref-20261018`. Protected health/assets and unauthenticated HTTP 401 passed. The live browser switched Warning on (both flags saved true), then off (both saved false), and reloaded to verify the persisted off state. Original preferences, audio, transcript, topics, all 16 memories and identity were preserved. Preference changes can mark advisory memory-stage reports stale, without changing source facts. Safe report: `artifacts/cloud-deploy/warning-pref-live-verification-20261018.json`. Proof: `artifacts/cloud-deploy/warning-preference-live-20261018.jpg`.

### Support display times and generic manual display

All four support types now expose their saved one-off display target, earliest known automatic display, and last released wording timestamp in Hong Kong (UTC+8). Unknown times have a reason instead of an invented timestamp. **Edit display time** and **Display Proactive Support Now** sit beside each other for each type; the old Comfort-only button is removed. Future edits postpone current cards; explicit manual display preserves the saved schedule and global settings. Source/identity, version and permission checks remain, including fictional-warning controls. Schedules are evaluated on app visits/polls, not by operating-system notifications or a daily recurring timer.

The backend stores schedules in an additive `support_display_times` table, with normalized timezone values and stale-edit rejection. Current, safe wording can be reused when a delayed card is released, and future-scheduled types do not block another due type through the automatic ambiguity rule. The release is deployed on 2026-10-18: `artifacts/cloud/hear-you-are-timing-20261018.tar.gz`, 106 files / 296,497 archive bytes, SHA-256 `c90cdff21fd0e089c71f56e66ccbc56c49ecee7377eddcb0473a6d89648d3b22`. Secret scanning passed and no credentials, application data, audio or model weights were packaged. This exact source archive remains unchanged by the later verification record. Assets are `index-B-l1EyTL.js` and `index-8SIHFkCt.css`.

Validation passed 715 Python tests in 74.28 seconds, 62 frontend helpers, production build and pip check. The candidate image passed the extended disposable upload smoke, including metadata for all four types, future edits postponing an existing card, stale schedule rejection, and generic manual display preserving schedule and evidence. The targeted API suite also covers per-type permission gates, actual readiness timestamps, delayed text generation, schedule changes during HTTP work, one-off fulfillment, and feedback/snooze/cooldown projections.

Cloud rollout completed healthy; source/stopped-data backups remain under `/root/hear-you-are-timing-20261018`, with previous image `hear-you-are-app:before-timing-20261018`. The additive schedule table does not overwrite memories or preferences. Protected health/assets and unauthenticated HTTP 401 passed. The browser saved a temporary 22:00 Hong Kong target, verified that the current Comfort card disappeared, used the generic Display Now button to show the same ID and cached wording while the target remained saved, then restored Automatic timing and left the card visible. All four final targets are Automatic, with the original recording, all 17 memories, identity and settings unchanged. Report: `artifacts/cloud-deploy/timing-live-verification-20261018.json`. Browser proof shows all four timing panels and adjacent controls: `artifacts/cloud-deploy/proactive-display-times-live-20261018.jpg`.

### Requested Comfort and delivery status

The Comfort source match can be true while automatic delivery is paused by snooze or the minimum interval. The revised per-type tile exposes its actual delivery status/reason and offers **Comfort me now** when matching confirmed sources and the enabled preference allow it. The dedicated endpoint generates/reuses one source-version-bound card, keeps it visible through state retrieval, and leaves automatic quiet/snooze/frequency settings unchanged. Source changes, disabled preferences and feedback still invalidate/hide it. DeepSeek failures are reported rather than displayed as successful support.

Local verification passed the 675-test Python suite, the final 17-case requested-Comfort API suite, pip check, and the extended disposable upload-to-card smoke. The final API suite includes concurrent one-call requests, unchanged settings during pauses, source edits/deletions during model generation, feedback with global pauses cleared, and original automatic-receipt preservation when an existing card is requested directly. Both source updates are deployed on 2026-10-18. The final wording adapter passed 29 focused tests, and the final UI passed 53 helpers plus production build. Source packages are `hear-you-are-comfort-20261018.tar.gz` (SHA-256 `df51f9028462d3da291798db5967d15a76af7ae0e480a814e84d232932af7b8c`) and final `hear-you-are-comfort-text-20261018.tar.gz` (103 files / 283,497 bytes, SHA-256 `1660bf928d6adbd12deb2124cc901ba013868194e445eed936145a83d1ee7ff2`). They exclude secrets, audio, application data and model weights; these exact archives remain unchanged by this verification record. Assets are `index-DAMg3oFt.js` and `index-CJOusBu6.css`.

Both candidate images passed the extended disposable smoke and restarted healthy. Existing source/stopped-data backups and images remain under their corresponding `/root/hear-you-are-comfort[-text]-20261018` paths and `before-comfort[-text]-20261018` image tags. The final update refreshed only the already requested card wording through exact-source-version checks, retaining its ID; no new identity confirmation or fact change occurred. Actual DeepSeek produced 319 characters of presentation-related reassurance. Authenticated health/assets, unauthenticated HTTP 401, repeated polling and repeated explicit requests passed. The original recording, all 17 memories, identity and settings matched the pre-release snapshot. Safe report: `artifacts/cloud-deploy/comfort-live-verification-20261018.json`. The browser's explicit button focused the actual visible card; the final private screenshot is `artifacts/private/proactive-requested-comfort-live-20261018.jpg`.

### Automatic daily summaries release

The missing daily-card generation hook is fixed and deployed on 2026-10-18. Existing memory stages already extracted and organized provisional facts; daily prose had remained behind the old manual analysis endpoint while the UI claimed it was being prepared. The new date/source-version coordinator schedules DeepSeek summaries automatically after speaker confirmation, on state retrieval, and following relevant source edits/deletions. It exposes real queued/running/failed/unconfigured/ready/no-sources states; retries use the same job, source changes are checked again before commit, and repeated polling reuses cached results. No additional generation or interpretation-review click is required on the successful path. Consolidation's existing LLM duplicate/conflict proposals remain advisory; this release adds the missing downstream summary step.

Exact deployed source: `artifacts/cloud/hear-you-are-daily-20261018-v2.tar.gz`, **102 files / 272,758 archive bytes**, SHA-256 `2f2e5a982f40e50f4befaa8f0328e46614de09aa7c21810e7dade88fcff159d3`. It includes the final synchronized regression tests and the new `backend/hya/daily_summaries.py`. Secret scanning passed; no credentials, application data or model weights were uploaded. Production assets are `index-f3j_mBr4.js` and `index-ChJHMkQ7.css`. Earlier prepared non-v2 archive was not deployed. The exact v2 archive remains unchanged by this later verification record.

Validation passed **659 Python tests, 47 frontend helper tests, production build and pip check**. The extended disposable upload smoke passed locally and in the cloud candidate: zero summary calls before identity confirmation; three automatic daily categories after confirmation; one call across repeated polling; and a second generation following a versioned fact edit, with raw segments preserved. The targeted API regressions also cover incomplete model responses, cached failures, explicit retry, source membership/category changes, deletion during generation, and transient source-status recovery.

The production app restarted healthy, retaining the existing authenticated domain and named data/model volumes. Previous source/stopped-data snapshots remain under `/root/hear-you-are-daily-20261018/` and the previous image is tagged `hear-you-are-app:before-daily-20261018`; no data migration was required. External verification passed protected health/assets and unauthenticated HTTP 401. A GET/state check automatically generated all **three real DeepSeek daily summaries** from the existing confirmed recording and reached `summary_generation.status=ready`, without any Generate, speaker-confirmation or interpretation-review submission. Audio, transcript, all **15** memories, identity and preferences matched the pre-release snapshot. Repeated polling preserved the generation timestamps. Safe verification: `artifacts/cloud-deploy/daily-live-verification-20261018.json`; candidate proof: `daily-server-verified-20261018.jpg`. The browser visibly shows the three completed AI summaries with Sources/Edit topic actions; user-content screenshot remains private at `artifacts/private/daily-summaries-live-20261018.jpg`.

### Source-grounded proactive support and card wording fixes

The 2026-10-18 trigger fix is deployed: uncorrected DeepSeek atoms can match their exact, current, confirmed source quote even when the atom uses third-person wording. It preserves user corrections and rejects reported/hypothetical request contexts. Proactive tiles now show each type's actual matching reason. The trigger/UI release passed 627 Python tests and 37 frontend helper tests; the updated full upload smoke reproduces the former failure and passes locally and in the cloud candidate.

Trigger archive: `artifacts/cloud/hear-you-are-care-20261018.tar.gz`, 98 files / 262,325 bytes, SHA-256 `3585603568597b5d7b306b9126bac9e28f816b683c0f37ba57b2d2f7c5bdbf23`. Live verification caught a separate model wording defect: warning-only fictional-demo instructions contaminated a real Comfort card. The final adapter includes those instructions only for warning cards and rejects fictional/demo mislabelling on real care. Its source is `artifacts/cloud/hear-you-are-care-text-20261018.tar.gz`, 98 files / 263,479 bytes, SHA-256 `535e0b105d0b5b66bb2047cb9f604615882f9705d7494acbae3405d1d09e9e29`. These exact archives remain unchanged by this later record. Both updates exclude secrets/audio/models and preserve named volumes; no data migration or semantic approval was performed. Backups and previous images are retained under the corresponding `/root/hear-you-are-care-20261018/` and `/root/hear-you-are-care-text-20261018/` releases.

The wording update passed a 639-test full run plus 25 focused adapter tests covering the final validation rules. Both cloud candidates passed the synthetic upload/topic/edit/automatic-care smoke. Actual hosted DeepSeek then produced one Comfort card from one source quote on the already confirmed recording. Its incorrectly labelled wording was refreshed using the same card ID and source-version checks. The original audio/transcript, all 17 atomic memories, identity confirmations and settings stayed byte-for-byte equivalent in the verification comparison (`artifacts/cloud-deploy/care-live-verification-20261018.json`). Repeated state polling returns the same card. Reminder reports `no_allowed_follow_up`; Suggestion reports `no_explicit_actionable_request`; Comfort reports `conditions_met`. Browser proof containing user content remains private at `artifacts/private/proactive-comfort-live-20261018.jpg`.

These remain narrow English demonstration rules. Reminder's current UI exposes bedtime scheduling from sleep-pattern cards; general task scheduling and automatic permission/time inference are not implemented. Suggestion matches supported practical-help requests or the existing repeated-poor-sleep pattern, rather than every expression of stress. DeepSeek writes the card after deterministic source/type/permission checks pass.

### Automatic upload and editable topics release

Deployed on 2026-10-18 from `artifacts/cloud/hear-you-are-flow-20261018.tar.gz`: **94 files, 255,496 archive bytes**, SHA-256 `93552883e399f32ccd9e4e7fff5e13ae0b2e289a8ac49e96355a1806098f6690`. Secret scanning passed; credentials, recordings and model weights were excluded. This exact deployed archive is retained unchanged by later verification-document edits. Production assets are `index-BcAUY1fc.js` and `index-BLaRNJ3B.css`.

Local checks passed **602 Python tests, 32 frontend helper tests, production build, pip check**, and the isolated complete upload/edit/support smoke test (`artifacts/cloud-deploy/flow-local-verification-20261018.json`). The cloud candidate repeated that smoke test in a disposable database, including automatic support without interpretation approval and single-call generation. It used synthetic ASR/model doubles; the separate hosted browser check below used the actual models.

The release is healthy and protected HTTPS/assets/authentication checks passed (`flow-external-verification-20261018.json`). At migration time the live workspace contained one recording and 17 memories; their raw audio, transcript, identity and memory content were preserved. Their dates were aligned to persisted server upload time, and five existing topics were preserved without re-extraction (`flow-existing-topics-20261018.json`). Previous source/stopped-data snapshots are retained under `/root/hear-you-are-flow-20261018/`, with rollback image `hear-you-are-app:before-flow-20261018`. Automatic rollback restores source and image only: the backward-compatible date/version migration is forward-only, protecting possible new user writes; the stopped-data snapshot remains available for deliberate recovery.

A fresh **browser upload** of `Synthetic-upload-flow-verification.wav` completed actual hosted SenseVoice and DeepSeek processing after a single Upload and process click: **six transcript segments, six atomic memories and three numbered topics**, with canonical server upload time. No additional processing, speaker confirmation or semantic-approval click was submitted (`flow-live-upload-20261018.json`). The live topic editor then saved a synthetic topic title and atomic fact, and reopening it showed the new text and version while speaker identity remained unknown. Existing user facts were not edited. Browser proof: `flow-topics-live-20261018.jpg` and `flow-editor-live-20261018.jpg`; deployment proof: `flow-server-verified-20261018.jpg` under `artifacts/cloud-deploy/`.

The protected demo on Tencent Lighthouse Hong Kong (`ap-hongkong-3`) at **https://hear-you-are.43-161-239-58.sslip.io** was updated on 2026-10-18 to **SenseVoiceSmall INT8 CPU + mandatory local speaker diarization + official DeepSeek**. The existing authenticated Tencent TAT console connection resolved the earlier access obstacle; no new credentials were created. The production app was recreated and is healthy, with authentication/domain/data retained and old source plus a stopped-app data snapshot saved. External verification passed unauthenticated HTTP 401 and authenticated health/frontend/JS/CSS HTTP 200: `artifacts/cloud-deploy/sensevoice-external-verification-20261018.json`. After the user completed login, Chrome verified Daily cards, Preferences and Import recording, showing the server CPU/SenseVoice/anonymous-label status and configured DeepSeek. Upload controls were visible, with import disabled until file selection. No file was uploaded, no new transcription was run and no preferences were changed. Screenshot: `artifacts/cloud-deploy/sensevoice-browser-verified-20261018.jpg`.

All four official DeepSeek keys authenticated against `/models` (HTTP 200), and a live synthetic-text test on 2026-10-18 completed all four semantic stages plus card generation: **3 memories and 3 cards**, with JSON/source validation passed and understanding still requiring user review. The local report is `artifacts/private/deepseek-live-20261018-173349.json`. No real app data/audio was used and no ASR request was made.

The selected CPU pipeline combines [SenseVoiceSmall INT8](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html), Silero VAD and [Sherpa-ONNX offline diarization](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html) with pyannote segmentation 3.0 / CAM++ Chinese-English embeddings. On 2026-10-18 it passed a native Windows CPU test with 25.75 seconds of synthetic English in A/B/A two-voice order: **3.099 seconds** for full VAD/diarization/transcription, **454.3 MiB** peak process working set, six segments and two anonymous clusters, with A returning to its original cluster. Synthetic WER was 0; ASR/VAD/diarization threads were 4/4/2, with no GPU, external ASR or real application data. Identities remained unknown/unconfirmed. The private report is `artifacts/private/sensevoice-asr-20261018-102728.json`. This single local fixture does not establish real-conversation accuracy or the VM's capacity/latency.

A second local run of that synthetic audio completed ASR/source storage and the four required DeepSeek semantic stages (`artifacts/private/sensevoice-asr-20261018-102904.json`); optional raw quality was skipped. It did not generate cards; validated card JSON initially came from the separate synthetic text test above.

The new candidate image subsequently passed the same fixture **on the actual 4-vCPU/8-GB VM**: VAD/diarization/ASR took **4.338 seconds**, peak process working set was **472.3 MiB**, and six segments/two clusters retained A/B/A recurrence with synthetic WER 0. Per-turn coverage exceeded 60% and dominant-speaker agreement exceeded 80%. All four DeepSeek stages and validated card JSON passed, generating six memories and three cards; total verifier time was **13.656 seconds**. The verifier used only a disposable `/tmp` database and synthetic identity simulation. Semantic review remained pending and proactive output stayed gated. No production data was read and no external ASR was called. This verifies one synthetic candidate-image path, not real-conversation quality, sustained capacity or the browser upload flow. The local full suite passed 473 tests in 20.82 seconds; `pip check` passed.

The local metrics capture, transcribed from authenticated server terminal output, is `artifacts/cloud-deploy/sensevoice-server-verification-20261018.json`; it is not a raw report download. The complete report remains on the server at `/var/cache/hear-you-are/verification/sensevoice-server.json`.

The subsequent **topic grouping/select-all update** is deployed from `artifacts/cloud/hear-you-are-topics-20261018.tar.gz` (84 files, 229,249 bytes; SHA-256 `28637e5e04018dfc56bc1559bb83196bb0ff0e9239e460220a215c4e1ae83209`). These are the exact deployed source bytes, with no secrets or model weights; later documentation edits do not rebuild that archive. Previous source and stopped-app data snapshots are retained under `/root/hear-you-are-topics-20261018/`; the previous image is tagged `hear-you-are-app:before-topics-20261018`.

Feature verification is separate from the earlier ASR benchmark: six synthetic text fragments became two three-fragment topics in 8.703 seconds locally, preserving raw sources and unknown/unreviewed state (`artifacts/private/topics-live-20261018.json`). The cloud candidate semantic check passed in 6.39 seconds at `2026-10-18T19:10:22.825033+08:00`; its raw report remains at `/var/cache/hear-you-are/verification/topics-server.json`, not yet downloaded. No ASR was rerun for this check. Topic-release checks passed 534 Python tests in 42.15 seconds, 19 frontend helper tests, production build and `pip check`. External protected HTTPS/new JS checks passed in `artifacts/cloud-deploy/topics-external-verification-20261018.json`; rollout completed healthy.

The cloud browser check separately used the existing synthetic recording's four stored fragments, organized into two topics of two fragments each. Processing preserved raw previews and disabled all selection/confirmation controls. Ready-state select-all (4/4), single-fragment override (3/4; global/topic mixed), clear (0/4), first-topic selection (2/4) and partial-to-global selection (4/4) all passed. It ended cleared at 0/4, without identity confirmation or semantic approval. No new upload, ASR, settings change or deletion occurred. Evidence: `artifacts/cloud-deploy/topics-select-all-20261018.jpg`, `topics-selection-verified-20261018.jpg`, `topics-review-ready-20261018.jpg` and terminal proof `topics-server-verified-20261018.jpg`.

### Recording date and interpretation review release

Deployed source: `artifacts/cloud/hear-you-are-dates-20261018.tar.gz`, 89 files / 244,725 bytes, SHA-256 `ab2e3f807dbea08fa90df1e2c700fed743f4486e868001ee97e893a82aa29504`; secret scanning passed. This exact archive remains unchanged by later documentation edits. Rollout completed healthy under `/root/hear-you-are-dates-20261018/`, retaining previous-source/stopped-data backups and rollback image `hear-you-are-app:before-dates-20261018` under the existing safeguards.

Completed local checks: 578 Python tests in 34.30 seconds, 34 frontend helper tests, production build (`index-5B23yirj.js`, `index-FIll9Cyp.css`), `pip check` and npm production audit with zero vulnerabilities. `artifacts/cloud-deploy/dates-local-verification-20261018.json` passed isolated synthetic date/review/CAS/no-op/generation-gate checks, including cancellation of old approval after a date change; no application data, ASR or external provider was used. User recording timestamps/content are excluded from this public deployment record.

The cloud candidate date check passed at `2026-10-18T11:26:54.050189+00:00` without ASR/provider calls. Protected HTTPS, unauthenticated rejection and the new JS/CSS passed (`artifacts/cloud-deploy/dates-external-verification-20261018.json`). A recording date was saved through the live UI with original evidence and speaker confirmation preserved. The guide/cards expose Generate AI summary, including pending placeholders with review tokens; unrelated undated evidence does not block the dated cards. Three generated AI interpretations are now visible and await user review, with active That's right, Needs changes and View sources actions. No semantic approval or source correction was submitted. Terminal proof is `artifacts/cloud-deploy/dates-server-verified-20261018.jpg`; actual recording date/content and interpretation screenshots remain private.

Anonymous labels distinguish voices within a recording, while personal identity stays unconfirmed; overlapping/uncovered speech stays unknown. Segment times are speech/speaker-boundary estimates, with **no word timestamps**. Earlier Tencent activation is historical: postpaid/training-data consent stayed off, and that path was abandoned before backend key creation or live ASR.

## 1. Prepare the VM and source

The following provisioning instructions are for a new VM. The existing Hong Kong project is `/root/hear-you-are`, with Compose project name `hear-you-are`; it already has Docker, HTTPS authentication and persistent data. Preserve those when updating it, rather than purchasing or provisioning another instance.

Install Docker Engine and the Compose plugin using the [official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/). Confirm `docker compose version` works; prefix Docker commands with `sudo` if needed.

Set the domain's DNS A record to the VM's public IPv4 address. Only publish an AAAA record when that IPv6 address is also served. Allow SSH from your own IP and inbound TCP 80/443 in the provider firewall. UDP 443 is optional for HTTP/3. Leave port 8765 closed.

Package the current source from PowerShell in this project root. The explicit file list includes deployment source and empty configuration examples. Never copy local `.env` files, installed environments, recordings or databases:

```powershell
tar.exe -czf "$env:TEMP\hear-you-are-cloud.tar.gz" --exclude=node_modules --exclude=dist --exclude=__pycache__ --exclude=.env --exclude=*.sqlite* Dockerfile .dockerignore compose.yaml requirements.txt requirements-sensevoice.txt .env.example backend web scripts/download_sensevoice_models.py scripts/download_qwen_models.py scripts/verify_deepseek.py deploy/Caddyfile deploy/.env.example docs/CLOUD-DEPLOYMENT.md docs/MEMORY-PIPELINE.md
scp "$env:TEMP\hear-you-are-cloud.tar.gz" ubuntu@YOUR_SERVER:~/
```

On the VM:

```bash
mkdir -p ~/hear-you-are
tar -xzf ~/hear-you-are-cloud.tar.gz -C ~/hear-you-are
cd ~/hear-you-are
if [ ! -f deploy/.env ]; then cp deploy/.env.example deploy/.env; fi
chmod 600 deploy/.env
```

Use fresh data for a new deployment. Preserve the existing Linux demo's named data volume during its update. Importing a Windows database is a separate migration because its rows contain absolute Windows audio paths and need the corresponding audio files.

## 2. Configure access and providers

Generate a password hash interactively and edit the server-only environment file:

```bash
docker run --rm -it caddy:2-alpine caddy hash-password --algorithm bcrypt
nano deploy/.env
```

Enter a strong password at Caddy's prompt. [Caddy documents interactive input](https://caddyserver.com/docs/command-line#caddy-hash-password). Set `HYA_DOMAIN`, `HYA_DEMO_USER` and the complete generated `HYA_PASSWORD_HASH`. Keep the hash single quoted, such as `HYA_PASSWORD_HASH='$2a$14$...'` with the actual complete value: [Compose preserves single-quoted values literally](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/).

Configure the providers:

- Install the CPU runtime from `requirements-sensevoice.txt`. Set `HYA_SENSEVOICE_MODEL_PATH`, `HYA_SENSEVOICE_TOKENS_PATH` and `HYA_SILERO_VAD_MODEL_PATH` to `/var/cache/hear-you-are/sensevoice/{model.int8.onnx,tokens.txt,silero_vad.onnx}` respectively. No speech API key, Torch, GPU or forced aligner is required. These are container-visible paths; Windows paths cannot be reused on Linux.
- VAD and diarization are mandatory in this provider. Set the speaker paths to `/var/cache/hear-you-are/diarization/segmentation.onnx` and `.../embedding.onnx`; Compose uses those defaults on `hya_models`. The helper downloads the assets and preserves model licensing files; SenseVoice weights use their own FunASR model license. Default clustering threshold is `HYA_DIARIZATION_THRESHOLD=0.5` and speaker CPU threads are `HYA_DIARIZATION_NUM_THREADS=2`. Missing required models/runtime leave the entire speech path unavailable.
- Keep `HYA_DEEPSEEK_BASE_URL=https://api.deepseek.com` and `HYA_DEEPSEEK_MODEL=deepseek-flash` for the [official DeepSeek API](https://api-docs.deepseek.com/). Set server-side official credentials and verify a real request before treating the model as available.
- Assign the four official keys to `ATOMIC`, `CONSOLIDATION`, `RETRIEVAL` and `GOVERNANCE`. Leave the optional extra `RAW_EVIDENCE` key empty to skip transcript-quality review. See [the memory pipeline contract](MEMORY-PIPELINE.md) for variable names and JSON rules.

Compose sets `HYA_ASR_PROVIDER=sensevoice`, `HYA_MEMORY_MODE=deepseek` and `HYA_ALLOW_EXTERNAL_LLM=true`. ASR defaults are 30-second maximum recognition windows and 4 CPU threads. There is no automatic Qwen/Whisper/cloud-ASR/local-rule fallback. The on-demand card button reuses the DeepSeek base/model/retrieval key unless `HYA_LLM_*` overrides are provided.

Missing local speech models/dependencies and missing DeepSeek credentials are reported separately in Preferences; a running web process does not prove inference works. Caddy still requires a domain, username and password hash. Keep text-model secrets server-side. Use `config --quiet` below rather than printing resolved configuration containing credentials.

For the existing server, retain `HYA_DOMAIN`, `HYA_DEMO_USER` and `HYA_PASSWORD_HASH` in `deploy/.env`. Merge provider fields privately through verified SSH/SFTP or the authenticated cloud console; do not pass secret values as command arguments or print resolved environment files. The completed update used the existing Tencent TAT console connection. Configure an approved SSH login and host-key pin before using the optional local SSH helper; do not bypass verification.

## 3. Build and start

Run from the project root:

```bash
docker compose --env-file deploy/.env config --quiet
docker compose --env-file deploy/.env build
docker compose --env-file deploy/.env run --rm --no-deps app python scripts/download_sensevoice_models.py --directory /var/cache/hear-you-are
docker compose --env-file deploy/.env run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose --env-file deploy/.env up -d
docker compose --env-file deploy/.env ps
docker compose --env-file deploy/.env logs --tail=100 app caddy
```

The multi-stage image builds React with Node 22 and installs the CPU Sherpa/backend runtime in Python 3.12. Its nonroot runtime uses separate data/model volumes; the explicit download command fills `/var/cache/hear-you-are/{sensevoice,diarization}`. Both download scripts must be present because the SenseVoice helper imports shared utilities from `download_qwen_models.py`; it does not import Hugging Face or fetch Qwen models. Inference does not download missing assets. Only Caddy publishes ports; HTTPS and [password protection](https://caddyserver.com/docs/caddyfile/directives/basic_auth) cover UI/API. The outer 110 MB body cap accommodates **100 MiB audio** plus multipart overhead. This image passed the bounded synthetic host check above; longer recordings and simultaneous workloads require separate measurement.

## 4. Verify actual behavior

Open `https://YOUR_DOMAIN`. A browser without credentials must request the shared login. Check health through the protected URL; `curl` prompts for the password:

```bash
curl --user YOUR_DEMO_USER https://YOUR_DOMAIN/api/health
```

Health reports configuration and process availability, not a successful provider request. Verify the complete path:

The text-only preflight can be reproduced before deployment using [`scripts/verify_deepseek.py`](../scripts/verify_deepseek.py) in the configured project Python environment. It makes **billable live requests** against synthetic text and a disposable database, and does not read real app data or call ASR. See [the memory pipeline verification record](MEMORY-PIPELINE.md#current-verification-boundary). It does not replace the following hosted audio test:

The local synthetic CPU result above can be reproduced on Windows with `python scripts/verify_sensevoice_asr.py --run`; it generates two offline voices and uses temporary app data. `--with-deepseek` additionally makes billable synthetic text requests. Repeat complete CPU checks on the deployment hardware with suitable generated and real non-sensitive speech; measure memory/latency, estimated segment boundaries, speaker changes and overlap/unknown handling. Older Tencent/Qwen/Whisper tests and DeepSeek-only passes do not replace host verification. Health's `speaker_diarization=true` indicates capability, not a benchmark; `word_timestamps=false` makes the segment-level timing limitation explicit.

1. Upload a short non-sensitive English recording, at most **100 MiB and 20 minutes**. In hosted mode it is stored/transcribed on the demo server; preserve the original source bytes.
2. Review transcript text, estimated speech/speaker segment times and `Speaker 0` / `Speaker 1` labels against the recording. There are no word timestamps. Labels apply within this recording and do not identify you; overlapping/uncertain speech stays unknown.
3. Wait for automatic DeepSeek extraction and topic organization after **Import and transcribe**. The saved server timestamp appears as **Uploading date**. Resolve stage errors using retry; successful transcription remains available when later memory processing fails.
4. Check the numbered topics and use **Edit** to correct a topic title, summary or its atomic facts. Original transcript text stays unchanged. Existing transcripts can be organized without another ASR run or duplicate extraction.
5. Confirm which source segments are yours. Matching proactive support then generates automatically when its existing permission and condition checks are satisfied; there is no interpretation-approval step. Optional daily summaries remain source-linked. Consolidation/governance proposals do not grant permissions, merge memories or delete evidence.
6. Restart the app and verify audio, transcript, memories and settings persist:

```bash
docker compose --env-file deploy/.env restart app
```

An interrupted job needs reprocessing/retry. Keep exactly **one app container and one Uvicorn worker**: model loading, SQLite mutation locks and background task coordination are held in one process. Multiple replicas are not supported. Check that model memory and transcription latency remain acceptable while the UI is active.

## Updates and backups

For the existing Hong Kong deployment, transfer updated source without overwriting `deploy/.env`, merge the provider configuration privately, then validate and rebuild only the app while retaining Caddy and the named data volume:

```bash
docker compose --env-file deploy/.env config --quiet
docker compose --env-file deploy/.env up -d --build --no-deps app
```

Named volumes retain app data (`hya_data`), model cache (`hya_models`) and Caddy certificate/configuration state (`caddy_data`, `caddy_config`) on this VM. They are not off-server backups. A cache volume from the old Whisper deployment may contain different checkpoint formats; its existence does not mean the selected SenseVoice/VAD/speaker weights are available.

Back up the data volume to protected off-server storage with the app stopped, or use a SQLite-aware method that handles WAL files. Preserve Caddy's certificate state and protect backups containing recordings or private keys. Use `docker compose --env-file deploy/.env stop app` before a filesystem backup and `docker compose --env-file deploy/.env start app` afterward. Avoid `docker compose down -v` during updates because it deletes named volumes. Copying only `memory.sqlite3` while the app writes can omit WAL changes.

The SenseVoice + official DeepSeek update is live with protected HTTPS, authenticated browser navigation and the synthetic candidate-image recording-to-card check completed. New browser uploads, real-conversation accuracy and representative duration/concurrency still require separate checks before treating this single fixture as evidence of broader operating capacity.
