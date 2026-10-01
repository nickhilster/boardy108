$ErrorActionPreference = 'Stop'
$root = 'D:/DEV/Legion-PanelForge/outputs/wallpaper-variations-2026-09-25'
$briefDir = Join-Path $root 'briefs'
$desktopDir = Join-Path $root 'desktop'
$mobileDir = Join-Path $root 'mobile'
$requestDir = Join-Path $root 'requests'
New-Item -ItemType Directory -Force -Path $briefDir,$desktopDir,$mobileDir,$requestDir | Out-Null
$briefs = @(
  'A contemplative Ganesha-like guardian emerging from a midnight ink ocean, concentric sound-wave halos and tiny stars, luminous indigo, copper, and rose-gold accents, elegant negative space, premium editorial wallpaper, no text.',
  'A monumental geometric elephant deity built from translucent stained glass, refractions and prisms, deep cobalt background with saffron and magenta light, symmetrical sacred architecture, richly detailed wallpaper, no text.',
  'A playful cosmic Ganesha formed from orbiting planets and mathematical arcs, vivid electric violet, cyan, and warm gold, centered silhouette with generous breathing room, polished digital illustration, no text.',
  'A terracotta-and-ochre Ganesha statue in a monsoon garden of lotus ponds and waterfalls, cinematic dawn haze, layered depth and intricate carved patterns, calm luxurious wallpaper, no text.',
  'A black-hole Ganesha made of swirling graphite dust and ember-orange accretion light, dramatic chiaroscuro, elegant circular composition, high-contrast contemporary art wallpaper, no text.',
  'A bioluminescent ocean-current Ganesha, coral and sea-glass forms flowing through the body, teal, turquoise, midnight blue, and pearl highlights, fluid abstract realism, no text.',
  'A paper-and-ink origami Ganesha unfolding into birds and geometric petals, cream paper, charcoal ink, vermilion and indigo accents, tactile mixed-media collage, no text.',
  'A futuristic microchip-city Ganesha with neural-forest circuitry, neon amber traces on deep slate, precise architectural detail, restrained cybernetic sacred geometry, no text.',
  'A waterfall negative-space Ganesha silhouette carved by streams of white water through dark volcanic rock, luminous sunrise, serene monumental landscape, no text.',
  'An impossible dream Ganesha assembled from mirrored rooms, floating stairways, tiny lamps, and a friendly mouse guide, surreal but harmonious, midnight plum and gold palette, no text.'
)
for($i=0;$i -lt $briefs.Count;$i++) { Set-Content -LiteralPath (Join-Path $briefDir ('wallpaper-{0:d2}.txt' -f ($i+1))) -Value $briefs[$i] -Encoding UTF8 }
$refs = @(
  'D:/DEV/Ganesha-108/images/066-ganesha-as-sound-waves.png',
  'D:/DEV/Ganesha-108/images/101-refracted-ganesha.png',
  'D:/DEV/Ganesha-108/images/050-ganesha-expressed-as-mathematics.png',
  'D:/DEV/Ganesha-108/images/001-ganesha-as-a-maze.png',
  'D:/DEV/Ganesha-108/images/052-ganesha-as-a-black-hole.png',
  'D:/DEV/Ganesha-108/images/078-ganesha-as-bioluminescent-ocean-currents.png',
  'D:/DEV/Ganesha-108/images/083-ganesha-as-origami-becoming-alive.png',
  'D:/DEV/Ganesha-108/images/087-ganesha-as-microchip-city-neural-forest.png',
  'D:/DEV/Ganesha-108/images/090-ganesha-as-waterfall-negative-space.png',
  'D:/DEV/Ganesha-108/images/107-impossible-ganesha.png'
)
$cfg = Get-Content 'D:/DEV/Legion-PanelForge/comic-pipeline/pipeline.config.json' | ConvertFrom-Json
$py = Join-Path $cfg.comfy_root '.venv/Scripts/python.exe'
$make = 'D:/DEV/Legion-PanelForge/comic-pipeline/make_panel.py'
for($i=0;$i -lt 10;$i++) {
  $name = 'wallpaper-{0:d2}' -f ($i+1)
  $brief = Join-Path $briefDir ($name + '.txt')
  $ref = $refs[$i]
  foreach($target in @(@('desktop',1536,864,$desktopDir),@('mobile',864,1536,$mobileDir))) {
    $kind,$w,$h,$out = $target
    $kindProject = Join-Path $root $kind
    New-Item -ItemType Directory -Force -Path (Join-Path $kindProject 'requests'),(Join-Path $kindProject 'outputs') | Out-Null
    if (Get-ChildItem $out -Filter "$name`_*.png" -File -ErrorAction SilentlyContinue) { Write-Host "Skipping existing $kind $name"; continue }
    Write-Host "Generating $kind $name at ${w}x${h}"
    & $py $make --project $kindProject --name $name --brief $brief --reference $ref --width $w --height $h --seed (9000 + $i*2 + $(if($kind -eq 'mobile'){1}else{0}))
    if($LASTEXITCODE -ne 0){ throw "Generation failed for $kind $name" }
    Get-ChildItem (Join-Path $kindProject 'outputs') -File | Move-Item -Destination $out -Force
  }
}
