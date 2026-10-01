# Local comic-frame pipeline

GPT-generated reference images establish canon for characters and locations.
Locally, this pipeline turns a panel brief plus those references into:

1. **Art direction** — Ollama/Qwen3-VL reads the brief and reference image(s)
   and returns structured panel JSON (camera, composition, lighting,
   continuity, positive/negative prompt) that stays consistent with the
   references.
2. **A rendered frame** — ComfyUI/FLUX.2 Klein takes that JSON plus the
   reference image and generates the panel locally, cheaply, on the RTX 4070.

## Machine constraints — run the GPU workloads sequentially

Ollama and ComfyUI both want the GPU, and this machine has one 12 GB card.
**Never run a director call and a render at the same time.**

- `direct_panel.py` already unloads the Ollama model in a `finally` block
  (`keep_alive: 0` + an explicit `/api/generate` unload call), so after it
  exits `ollama ps` should print no rows before you start rendering.
- ComfyUI is started with `--lowvram` and stays resident across renders — it's
  fine to leave it running for a whole session, just don't call the Ollama
  director while it's actively sampling if VRAM is tight.
- **ComfyUI's VRAM leak is now recovered from automatically.** Its low-VRAM
  dynamic offloading has a slow VRAM leak: one process reliably renders
  somewhere between ~3 and ~12 panels back to back (varies with output size)
  before hitting a "sticky" CUDA out-of-memory error, after which every
  subsequent call fails the same way -- including a plain health check --
  until the process is killed and restarted. `make_panel.py` and
  `render_project.py` both detect this automatically (any `RuntimeError` or
  `TimeoutError` from the ComfyUI-touching steps) and, by default, run
  `comfy_supervisor.restart_comfyui`: kill the wedged process, start a fresh
  one, wait for it to answer a health check, then retry the render once. No
  manual intervention needed for the common case. Pass `--no-auto-recover`
  to disable this and fail immediately instead (e.g. while debugging, so a
  real failure doesn't get masked by a restart). If recovery itself fails
  (or you've disabled it), the manual fallback is unchanged: kill the
  `main.py` process, confirm `nvidia-smi` shows VRAM back near baseline, and
  start it again.

## One-time setup: tell the pipeline where ComfyUI lives

Nothing machine-specific is hardcoded. Copy the example config and edit it:

```powershell
Copy-Item .\pipeline.config.example.json .\pipeline.config.json
# then set "comfy_root" to your ComfyUI install (forward slashes are fine)
```

`pipeline.config.json` is gitignored. Every key can also come from an
environment variable (`comfy_root` -> `PANELFORGE_COMFY_ROOT`), and an explicit
CLI flag beats both. Only `comfy_root` is required; `comfy_output_root`
defaults to `<comfy_root>/output`, and the URLs and director model default to
localhost / `qwen3-vl:8b`.

`health-check.ps1` reports which config file it used and everything it
resolved, so start there when something looks wrong.

## Per-session setup

PowerShell's default execution policy on this machine blocks running local
`.ps1` files directly (`& script.ps1` fails with "running scripts is
disabled"). Rather than changing the system execution policy, invoke the
scripts with `-ExecutionPolicy Bypass` for this process only:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File '.\health-check.ps1'
```

## Daily workflow

### One command: brief + references -> rendered frame

Start ComfyUI first (see step 2 below) and leave it running, then for each
panel:

```powershell
$py = Join-Path (Get-Content .\pipeline.config.json | ConvertFrom-Json).comfy_root '.venv\Scripts\python.exe'

& $py .\make_panel.py `
  --project 'E:\AI\ComicPipeline\projects\<your-project>' `
  --name panel-01 `
  --reference 'E:\AI\ComicPipeline\projects\<your-project>\references\some-reference.jpg' `
  --seed 42
```

A project is a folder with `briefs/`, `references/`, `requests/`, and
`outputs/` subfolders (see `projects/starter` for a worked example).
`--name panel-01` looks for `briefs/panel-01.txt` by default (override with
`--brief`). `--reference` is repeatable — every reference goes to the Ollama
director for context, but only the **first** one (or whichever you pass to
`--render-reference`) is what ComfyUI actually preserves pixel continuity
from. The finished PNG is copied into `<project>/outputs/`.

`make_panel.py` chains the director call (with its own retries and Ollama
unload) and the full ComfyUI render (upload → prepare → submit → poll to
completion) in one call, matching the sequential-GPU rule automatically. If
ComfyUI isn't running at all yet, it fails with "Start ComfyUI first" — go to
step 2 below. If ComfyUI *is* running but wedged (its known VRAM leak), it
restarts ComfyUI automatically and retries once; see Machine constraints
above.

### Manual step by step (what the one command does, for debugging)

#### 1. Direction: brief + references -> panel JSON

```powershell
# Run from this directory (the repo's comic-pipeline/).
$py = Join-Path (Get-Content .\pipeline.config.json | ConvertFrom-Json).comfy_root '.venv\Scripts\python.exe'

& $py .\direct_panel.py `
  --brief   'E:\AI\ComicPipeline\projects\starter\briefs\sample-panel.txt' `
  --reference 'E:\AI\ComicPipeline\projects\starter\references\sample-location.jpg' `
  --output  'E:\AI\ComicPipeline\projects\starter\requests\sample-panel.json'

ollama ps   # should print no rows once this returns
```

Pass `--reference` multiple times for multi-reference panels (e.g. a
character sheet plus a location sheet).

#### 2. Start ComfyUI (leave running in its own window)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File '.\start-comfyui.ps1'
```

This runs `main.py --listen 127.0.0.1 --port 8188 --lowvram` from
`E:\AI\ComicPipeline\ComfyUI`. Wait for `To see the GUI go to:
http://127.0.0.1:8188` before continuing.

#### 3. Render: panel JSON -> frame

In a second PowerShell window/tab:

```powershell
# Run from this directory (the repo's comic-pipeline/).
$py = Join-Path (Get-Content .\pipeline.config.json | ConvertFrom-Json).comfy_root '.venv\Scripts\python.exe'

& $py .\comfy_client.py health

& $py .\comfy_client.py upload `
  --reference 'E:\AI\ComicPipeline\projects\starter\references\sample-location.jpg'
# -> {"reference_name": "comic-pipeline/sample-location.jpg"}

& $py .\comfy_client.py prepare `
  --panel-json 'E:\AI\ComicPipeline\projects\starter\requests\sample-panel.json' `
  --reference-name 'comic-pipeline/sample-location.jpg' `
  --output 'E:\AI\ComicPipeline\projects\starter\requests\sample-workflow.json' `
  --seed 42 `
  --filename-prefix 'comic-pipeline/sample-panel'

& $py .\comfy_client.py submit `
  --api-workflow 'E:\AI\ComicPipeline\projects\starter\requests\sample-workflow.json'
# -> {"prompt_id": "<uuid>"}
```

Poll `http://127.0.0.1:8188/history/<prompt_id>` until
`status.completed == true`, then the frame is at
`E:\AI\ComicPipeline\ComfyUI\output\comic-pipeline\<filename_prefix>_00001_.png`.
A render on this machine takes roughly 15-20 seconds once the models are
loaded.

## Meme generator

A separate pipeline alongside the panel one: brief + a sourced meme template
-> Ollama/Qwen3-VL decides captions and whether to remix the artwork ->
optionally ComfyUI edits the template -> captions are composited locally with
Pillow. See `docs/MEME-SOURCES.md` for where templates come from and why only
Imgflip is automated today.

**One-time setup:** install Pillow into the same ComfyUI-venv Python
everything else here runs under:

```powershell
$py = Join-Path (Get-Content .\pipeline.config.json | ConvertFrom-Json).comfy_root '.venv\Scripts\python.exe'
& $py -m pip install -r requirements.txt
```

Captions need a font; the pipeline looks for Windows' bundled Impact font
automatically, or set `PANELFORGE_MEME_FONT` / a `meme_font` config key to
point at a different `.ttf`.

**CLI**, one meme at a time (mirrors `make_panel.py`):

```powershell
& $py .\make_meme.py `
  --project 'E:\AI\ComicPipeline\projects\<your-project>' `
  --name my-meme `
  --brief 'E:\AI\ComicPipeline\projects\<your-project>\briefs\my-meme.txt' `
  --seed 42
```

Add `--no-remix` to always caption-only, or `--template-id <imgflip id>` to
pin a specific template instead of a random pick from Imgflip's popular list.

**Local GUI**, for picking a template and typing a brief interactively:

```powershell
& $py .\meme_intake\server.py --projects-root 'E:\AI\ComicPipeline\projects'
```

Then open `http://127.0.0.1:8765/` in a browser. The server only binds to
`127.0.0.1` and only ever calls `make_meme.py`'s own function directly --
there's no separate approval step or async job queue, matching how
`make_panel.py` already runs as one synchronous call.

## Boardy 108 production

The [Boardy project operator guide](projects/boardy-108/README.md) covers the
approved official-page canon, preflight, three-image look-check review, and sequential production
in nine batches of twelve scenes. Each scene produces exactly one image. Use
`batch_runner.py` for this version-2 project; it records provenance and continues
other scenes after a scene fails. Render completion and human acceptance are
separate states. Show the three look-check images and obtain user approval
before full production. The source inputs are prepared; production images exist only
after actual rendering.

## Files

| File | Purpose |
| --- | --- |
| `make_panel.py` | One-command wrapper: brief + references -> directed, rendered frame, copied into the project's `outputs/`. |
| `direct_panel.py` | Brief + references -> validated panel JSON via Ollama (the default, local, free director). Retries up to 5x on a malformed response; unloads the model when done (`finally`). |
| `cloud_director.py` | Same brief-to-panel-JSON contract as `direct_panel.py`, but via a hosted model instead of Ollama -- `--director cloud` on `make_panel.py`/`render_project.py`. Provider-agnostic (a small `PROVIDERS` registry resolved by name from `pipeline_config`); Anthropic is the only one implemented so far, needs `ANTHROPIC_API_KEY` set. Leaves the pipeline's local/free/GPU-only path -- see Known limitations. |
| `panel_schema.json` | Strict JSON-schema contract for panel direction; passed to Ollama as `format`, and to `cloud_director.py`'s Anthropic provider as a forced tool-use `input_schema`. |
| `comfy_client.py` | ComfyUI health check, reference upload, workflow `prepare` (panel JSON -> API graph), `submit`, and `wait` (poll to completion). |
| `comfy_supervisor.py` | Kills and restarts a wedged ComfyUI process and waits for it to become healthy again. Windows-specific (shells out to PowerShell); not unit tested for that reason -- callers inject it via `make_panel`'s `recover` parameter so the retry decision logic stays testable. |
| `reference_catalog.py` | Validates `references.json`/`production.json` and resolves reference IDs to files for multi-panel productions. |
| `preflight_project.py` | Aggregated pre-render checks for a production (catalog + manifest validation) before spending any GPU time. |
| `render_project.py` | Renders every panel in a production manifest sequentially via `make_panel.make_panel`, with hash-based resume and stop-on-first-failure. |
| `start-comfyui.ps1` | Starts ComfyUI in `--lowvram` mode on the configured host/port. |
| `health-check.ps1` | Verifies model files, CUDA, the Ollama model, and ComfyUI reachability in one JSON report, and echoes the resolved config. |
| `pipeline_config.py` | Resolves machine-specific settings for the Python tools (flag > env > config file > default). |
| `pipeline-config.ps1` | The same resolution for the PowerShell launchers. |
| `pipeline.config.example.json` | Template to copy to the gitignored `pipeline.config.json`. |
| `make_meme.py` | One-command wrapper: brief + a sourced template -> directed, optionally remixed, captioned meme in the project's `outputs/`. |
| `direct_meme.py` | Template image + brief -> validated meme JSON (concept, remix decision/prompt, captions) via Ollama. Same retry/unload behavior as `direct_panel.py`. |
| `meme_schema.json` | Strict JSON-schema contract for the meme director's output; also passed to Ollama as `format`. |
| `meme_sources.py` | Fetches/normalizes/caches meme templates. Imgflip only for now -- see `docs/MEME-SOURCES.md`. |
| `meme_compose.py` | Composites captions onto the base/remixed image with Pillow (Impact-style font, auto-fit, stroke outline). |
| `meme_intake/` | Local loopback-only GUI (`server.py` + `static/index.html`) for picking a template and typing a brief. |
| `requirements.txt` | Pillow, needed only by `meme_compose.py`. |
| `tests/` | Unit tests (stub Ollama/ComfyUI/Imgflip servers, no GPU or live services required). |

## Known limitations

- **The cloud director is a real quality/reliability upgrade, but it isn't free or local.** Compared side by side on the same seeds and references, a cloud model directing panels through `cloud_director.py` produced JSON that validated on the first attempt every time, with none of the run-on/ungrammatical `positive_prompt` text Qwen3-VL occasionally produces under a dense brief -- and in at least one head-to-head test, the resulting renders were the user's clear preference. But every panel then costs a real API call (money, latency, a network dependency) instead of a local, free, GPU-only one. Treat `--director local` (Ollama) as the default for bulk/exploratory work, and `--director cloud` as the option for a piece that's going to be shown to someone, not a blanket replacement.
- **`negative_prompt` is advisory only.** FLUX.2 Klein's distilled ComfyUI
  workflow runs at `cfg=1.0` with a zeroed-out negative conditioning (the
  official template's design), so whatever the director writes into
  `negative_prompt` is never actually applied to the render — it exists for
  a human reviewer, not the model. If a panel needs to exclude or restrict
  something (a color count, a medium, "no photographic realism"), the brief
  must make the director state that requirement **affirmatively** in
  `positive_prompt` instead; `direct_panel.py`'s instructions to the model
  already say this, but a brief that leans on "no X" language without an
  affirmative equivalent can still under-specify the style.
- **No cross-panel character identity lock.** The pipeline only anchors
  continuity to whatever reference image(s) you pass in. A location
  reference keeps the environment consistent; a recurring character's exact
  look (hair, build, etc.) can still drift panel to panel unless you supply
  a dedicated, locked character reference image the way you would a
  location.
- **qwen3-vl:8b thinking-field quirk.** With `think: false` and a JSON
  `format` schema, the model sometimes writes the completed JSON into the
  response's `thinking` field instead of `content` (which comes back
  empty). `direct_panel.py` falls back to `thinking` whenever `content` is
  empty. The request also sets `repeat_penalty: 1.3` / `repeat_last_n: 128`
  — without them the model can fall into a repetition loop (e.g. "no
  visible color X shifts...") and produce truncated, invalid JSON.
- **Occasional malformed field.** The director occasionally leaves one
  required field empty or otherwise invalid (seen at roughly a 1-in-3 to
  1-in-5 rate on some briefs). `run_direction` retries the same request up
  to 3 times before giving up; if it still fails, just re-run the command.
  The same quirk can also produce output that's *structurally* valid but
  semantically degenerate -- observed live: a meme whose captions came back
  as the literal strings `"REPE"` and `"S"` (a truncated fragment, not a
  real joke), which `validate_meme()` has no way to catch since both are
  non-empty strings. This reproduced identically on the same seed, so it's
  a genuine per-template response quality issue, not one-off noise; a
  different seed (different random template, or a different `--seed` on
  the same template) resolved it. Deliberately not adding a length-based
  heuristic to catch this in validation -- a legitimately short caption
  (`"No."`, a number, one word) is common and valid, and a heuristic narrow
  enough to catch "REPE" without false-positiving on real short jokes isn't
  obviously possible; re-running with a different seed is the mitigation.
- **Meme remix has the same advisory-field trap as `negative_prompt`.**
  `comfy_client.build_reference_edit_workflow` reuses the exact same
  `cfg=1.0`/zeroed-negative mechanism as panel rendering, so `remix.enabled`
  memes are also only ever conditioned on `remix.positive_prompt` --
  `remix.locked_elements` is shown to a human reviewer but never applied.
  Anything essential to keep recognizable about the template must be stated
  affirmatively inside `remix.positive_prompt`, never relied on via
  `locked_elements` alone; `direct_meme.py`'s instructions to the model
  already say this.
- **Imgflip's public API has no "Hot" endpoint.** `meme_sources.py` fetches
  Imgflip's aggregate "popular" list (`get_memes`), not the live Top/Hot
  sort that only exists on imgflip.com's own web UI. See
  `docs/MEME-SOURCES.md` for the other sources considered and why they
  aren't automated yet.
- **The remix engine can't render legible text.** Observed live in a batch
  run: a remix prompt that asked for a "Chancellor" speech bubble, and
  another that asked for cricket scoreboards, both came back with garbled,
  illegible text baked into the artwork -- an ordinary diffusion-model
  limitation, not specific to this pipeline. `direct_meme.py`'s prompt now
  explicitly tells the director the remix engine cannot render legible text
  and to put any necessary text in `captions` instead, never in
  `remix.positive_prompt`.
- **Speaker-label leakage into captions.** Observed live on a two-character
  template (Evil Kermit): the director wrote captions as `"[Left] ..."` /
  `"[Right] ..."` instead of just the on-image words, and those literal
  bracketed labels got composited straight onto the image. Fixed with an
  explicit prompt instruction; `validate_meme()` has no way to catch this
  class of issue since a label-prefixed string is still a valid non-empty
  string.
- **Random template assignment can be a poor fit.** When no `--template-id`
  is given, `pick_template()` assigns a template at random from Imgflip's
  top ~100 rather than letting the director choose one that actually fits
  the brief -- occasionally producing a coherent-but-oddly-matched result
  (e.g. an unrelated stock-photo reaction template captioned for an
  institutional/policy joke). Not a bug, just a real limitation of "surprise
  me" template selection; passing `--template-id` sidesteps it.

## Tests

```powershell
# Run from this directory (the repo's comic-pipeline/).
$py = Join-Path (Get-Content .\pipeline.config.json | ConvertFrom-Json).comfy_root '.venv\Scripts\python.exe'
& $py -m pytest .\tests -q
```

All tests run against local stub HTTP servers and don't require Ollama,
ComfyUI, or a GPU to be running.
