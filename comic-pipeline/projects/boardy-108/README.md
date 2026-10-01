# Boardy 108 operator guide

This project contains the email's 108 scene briefs in nine ordered sets of
twelve. Each scene produces exactly one image. Preparing inputs or completing a
dry-run does not generate images. All GPU work is sequential: local Ollama
direction releases the model before ComfyUI rendering on the same GPU.

## Approved official-page reference

The user designated `D:\DEV\BoardyAnimated\boardy-landing-logo-transparent.png`.
Its exact bytes are staged as `references/boardy-canonical.png` and recorded in
`references.json`, with SHA-256
`85cecb75e26d98e765b473c7c762a919af7340f892ee6014925ceb22e3648bc5`.
The user confirmed that the official-page image governs the visual canon,
including simple dark blue circular eyes, friendly dark blue curved smile,
and red tie. It supersedes the email's no-eyes/no-tie instructions. Compatible
email traits remain: matte corrugated cardboard box construction with seams,
tape and imperfect edges; fitted blue suit/white shirt; consistent proportions
and silhouette. The user has specified that no still image may contain
eyebrows, including when an original scene brief mentions one. The
reference is `approved`, and its pixels have not been altered. `canon.json`
records this decision and is injected into local direction for every scene.

The image is already staged. To restore it from its original source,
copy the bytes and compare the digest above before running preflight:

```powershell
Copy-Item -LiteralPath 'D:\DEV\BoardyAnimated\boardy-landing-logo-transparent.png' -Destination '.\projects\boardy-108\references\boardy-canonical.png'
Get-FileHash '.\projects\boardy-108\references\boardy-canonical.png' -Algorithm SHA256
```

## Safe production sequence

Run these commands from `comic-pipeline` in this checkout. Machine configuration
is supplied by the gitignored `pipeline.config.json` (see the main README).
Use the same model and configuration for look-check and production so resume
can reuse samples when their inputs match. Samples 001 and 038 were directed
locally; the corrected 085 sample was directed by Codex as a cloud director, so
the normal local production run will render 085 again unless it uses matching
director settings.

```powershell
$py = Join-Path (Get-Content .\pipeline.config.json | ConvertFrom-Json).comfy_root '.venv\Scripts\python.exe'
$boardyProject = '.\projects\boardy-108'
& $py .\preflight_project.py --project $boardyProject
& $py .\batch_runner.py --project $boardyProject --dry-run
```

Preflight must report `ok: true` and nine batch records of twelve scenes.
Dry-run lists the 108 scheduled scenes without contacting services or writing
run artifacts. Start Ollama with the configured local model available, then
start ComfyUI and wait for its health check before the three look-check renders:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File '.\start-comfyui.ps1'
# In a second PowerShell window, after ComfyUI has started:
& $py .\comfy_client.py health
& $py .\batch_runner.py --project $boardyProject --scene boardy-001-landline-ringing
& $py .\batch_runner.py --project $boardyProject --scene boardy-038-ridge-at-sunrise
& $py .\batch_runner.py --project $boardyProject --scene boardy-085-tiny-parts-workbench
```

These are three existing scenes from varied contexts and batches: landline
ringing (First contact), ridge at sunrise (Curiosity), and tiny-parts workbench
(Craft). Render exactly one image per selected scene. Do not reroll or create
extra variants. Open the images under `outputs/set-01-first-contact/`,
`outputs/set-04-curiosity/`, and `outputs/set-08-craft/`, show all three to the
user, and wait for their explicit approval before full production. Review the
official-source identity, composition, and source brief for each sample. Only
after the user's decisions, record the corresponding output-bound reviews:

```powershell
& $py .\review_project.py --project $boardyProject --scene boardy-001-landline-ringing --status accepted --note 'User approved official-canon look-check and brief'
& $py .\review_project.py --project $boardyProject --scene boardy-038-ridge-at-sunrise --status accepted --note 'User approved official-canon look-check and brief'
& $py .\review_project.py --project $boardyProject --scene boardy-085-tiny-parts-workbench --status accepted --note 'User approved official-canon look-check and brief'
# Run only after the user has approved all three displayed images:
& $py .\batch_runner.py --project $boardyProject --resume
```

Replace `accepted` with `rejected` or `review-needed` when that is the human
decision. If any look-check is rejected or needs review, preserve it and wait
for user direction. Acceptance is recorded
against the current output hash. A new image needs a new review. Every rendered
image starts with review status `pending`; render completion alone does not
mean the collection is accepted. Review the remaining scenes using the same
`review_project.py` command and their manifest scene names.

The full command schedules all nine batches sequentially. It continues after
individual scene failures, exits nonzero when any scene fails, and retains the
partial run manifest. Resume skips only successful scenes whose input and
output hashes still match. To resume one batch:

```powershell
& $py .\batch_runner.py --project $boardyProject --batch set-01-first-contact --resume
```

Omit `--resume` to explicitly rerender that batch. Previous attempts and images
are preserved under `attempts/`. Brief changes require an explicit revision;
use `production_review.record_brief_revision(project, scene_name, revision_path)`
from Python, supplying a nonempty UTF-8 replacement brief. The original brief
remains retained, and resume detects the revised input. Do not silently rewrite
source briefs or reroll images to hide drift.

## Outputs, timing, and announcements

Images are in `outputs/<batch-id>/<scene-name>.png`; directed requests and per
scene provenance are in `requests/`; run summaries are in `runs/<run-id>.json`.
These three generated directories are ignored by Git. Canon, briefs, manifest,
reference metadata and approved reference image remain tracked. Attempt
archives, human reviews and explicit brief revisions are retained separately.

The CLI persists a real run ID before the first scene and immediately prints
it to stderr. Progress goes to stderr; the final JSON summary goes to stdout.
Record the WhatsApp start/finish receipt at the moment each announcement arrives
in a separate terminal, using the displayed run ID:

```powershell
& $py .\run_event.py --project $boardyProject --run-id '<displayed-run-id>' --event start
& $py .\run_event.py --project $boardyProject --run-id '<displayed-run-id>' --event finish
```

These commands record the current UTC receipt time and do not send WhatsApp
messages. Never backfill an earlier event time. Corrections append a new
receipt using `--correction-of '<prior-event-id>'`. The manifest records actual
run start/finish and elapsed seconds. `comparison_seconds: 355` represents the
5:55 reference value; it is separate from actual elapsed time.
