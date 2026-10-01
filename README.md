# Boardy 108

108 cinematic stills of Boardy, the cardboard-box-headed character, in nine sets of twelve.

- `outputs/set-01-first-contact` … `outputs/set-09-the-box` — the final images, one per scene.
  Scene 108 carries the official Boardy logo as an exact flat insert on the clapperboard slate;
  the raw render is in `outputs/_sources/`.
- `comic-pipeline/` — the local ComfyUI/FLUX.2 Klein pipeline and the `boardy-108` project:
  scene briefs, per-scene art direction (`directions/`), canon, and review records.

Canon: fitted navy suit, white shirt, red tie, matte cardboard head with two round eyes and a smile.
No eyebrows or brow-like marks in any still.

Run (ComfyUI running locally): `python comic-pipeline/batch_runner.py --project comic-pipeline/projects/boardy-108 --director cloud --cloud-provider session --resume`
