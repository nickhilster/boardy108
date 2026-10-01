$ErrorActionPreference = 'Stop'
$root = 'D:/DEV/Legion-PanelForge/outputs/krishna-shiva-variations-2026-09-25'
$briefDir = Join-Path $root 'briefs'; New-Item -ItemType Directory -Force -Path $briefDir | Out-Null
$sets = @{
  krishna = @(
    @('flute-dawn','Krishna-inspired blue guardian playing a flute beside a river at dawn, peacock-feather iridescence, saffron mist, lotus reflections, elegant devotional surrealism, sacred geometry halos, no text.','044-ganesha-as-gautama-buddha.png'),
    @('cosmic-rasa','A joyful Krishna-inspired figure dancing inside a galaxy of indigo and gold orbit lines, playful cosmic energy, luminous peacock colors, rhythmic concentric rings, polished visionary art, no text.','021-ganesha-in-the-visual-language-of-hexxone-audiorbits.png'),
    @('forest-monsoon','Krishna-inspired flute player in a monsoon forest, emerald leaves, cobalt skin, electric rain, fireflies and lotus ponds, cinematic painterly atmosphere with intricate patterning, no text.','060-ganesha-as-weather.png'),
    @('blue-hour-mural','A Krishna-inspired blue figure in a layered indigo mural, geometric lotus motifs, vermilion and gold accents, tactile paper grain, contemporary Indian poster art. Preserve a coherent single subject and composition. No text.','079-ganesha-as-ink-topography.png'),
    @('butter-moon','A serene blue Krishna-inspired child seated beneath a crescent moon, holding a small golden orb, indigo night sky and warm lamp halos, simple centered composition, polished devotional illustration. No text.','054-ganesha-made-of-flowers.png'),
    @('peacock-fractal','Krishna-inspired form emerging from a fractal peacock plume, turquoise, violet, emerald and gold, mirrored symmetry, glossy jewel-like detail, immersive abstract devotional wallpaper, no text.','101-refracted-ganesha.png'),
    @('river-of-sound','Krishna-inspired musician standing where a river becomes visible sound waves, silver-blue currents and copper light, spacious composition, meditative digital painting, no text.','066-ganesha-as-sound-waves.png'),
    @('celestial-chariot','Krishna-inspired guide on a luminous geometric chariot crossing a star field, deep navy, bronze and saffron, architectural precision, mythic contemporary illustration, no text.','061-ganesha-as-a-city.png'),
    @('lotus-mandala','A serene Krishna-inspired portrait built from layered lotus petals and concentric mandalas, sapphire, rose, amber and cream, high-detail mixed media, no text.','026-ganesha-1-concentric-circles-and-arcs.png'),
    @('dream-garden','Surreal Krishna-inspired figure in an impossible garden of floating stairs, vines, moons and peacock feathers, luminous plum and teal palette, harmonious dream logic, no text.','107-impossible-ganesha.png')
  )
  shiva = @(
    @('mountain-stillness','Shiva-inspired meditating presence carved into a Himalayan mountain, ash-gray stone, moonlight, thin blue aura, vast negative space and precise sacred geometry, no text.','001-ganesha-as-a-maze.png'),
    @('cosmic-dance','Shiva-inspired cosmic dancer surrounded by plasma arcs and star particles, electric cobalt, magenta and molten gold, dynamic circular rhythm, premium visionary wallpaper, no text.','003-ganesha-as-the-big-bang.png'),
    @('third-eye-sun','A symbolic Shiva-inspired face with a radiant third-eye sun, smoky indigo clouds, copper crescent moon, graphic symmetry and dramatic chiaroscuro, no text.','067-ganesha-as-light.png'),
    @('river-hair','Shiva-inspired silhouette with a celestial river flowing through the hair into a luminous valley, cool slate, cyan water and saffron sunrise, cinematic landscape, no text.','063-ganesha-as-a-river-system.png'),
    @('blue-throat','A sculptural Shiva-inspired bust with a deep blue throat glow, serpentine orbit, black stone and ember highlights, restrained sacred minimalism, no text.','068-ganesha-as-shadow.png'),
    @('ashes-and-stars','Shiva-inspired ascetic figure dissolving into ash, smoke calligraphy without writing, stars and a small crescent moon, monochrome charcoal with ember-orange light, no text.','086-ganesha-as-smoke-calligraphy-without-writing.png'),
    @('tandava-fracture','Shiva-inspired dancer breaking through a kintsugi cosmos, fractured gold lines, obsidian, violet and ice-blue light, high-energy geometric abstraction, no text.','091-ganesha-as-fracture-kintsugi-cosmos.png'),
    @('mountain-storm','Shiva-inspired guardian above a stormy mountain ocean, lightning shaped as tridents and spirals, graphite, silver and electric blue, monumental realism, no text.','060-ganesha-as-weather.png'),
    @('lingam-light','Abstract Shiva-inspired vertical light form rising from concentric stone rings, deep maroon, indigo and gold, meditative temple atmosphere, refined minimalist art, no text.','048-ganesha-expressed-as-geometry.png'),
    @('eternal-night','Shiva-inspired presence emerging from a black-hole eclipse, crescent moon, stars, smoke and blue fire, symmetrical cosmic portrait, richly layered contemporary mythology, no text.','052-ganesha-as-a-black-hole.png')
  )
}
$cfg = Get-Content 'D:/DEV/Legion-PanelForge/comic-pipeline/pipeline.config.json' | ConvertFrom-Json
$py = Join-Path $cfg.comfy_root '.venv/Scripts/python.exe'; $make='D:/DEV/Legion-PanelForge/comic-pipeline/make_panel.py'
foreach($set in $sets.Keys){
  $setRoot=Join-Path $root $set; $desktop=Join-Path $setRoot 'desktop'; $mobile=Join-Path $setRoot 'mobile'; $req=Join-Path $setRoot 'requests'
  New-Item -ItemType Directory -Force -Path $desktop,$mobile,$req | Out-Null
  $items=$sets[$set]
  for($i=0;$i -lt $items.Count;$i++){
    $slug,$briefText,$refName=$items[$i]; $name=('{0:d2}-{1}' -f ($i+1),$slug); $brief=Join-Path $briefDir "$set-$name.txt"; Set-Content -LiteralPath $brief -Value $briefText -Encoding UTF8
    $ref="D:/DEV/Ganesha-108/images/$refName"
    foreach($target in @(@('desktop',1536,864,$desktop),@('mobile',864,1536,$mobile))){
      $kind,$w,$h,$out=$target; if(Get-ChildItem $out -Filter "$name`_*.png" -File -ErrorAction SilentlyContinue){Write-Host "Skipping existing $set $kind $name"; continue}
      $kindProject=Join-Path $setRoot $kind; New-Item -ItemType Directory -Force -Path (Join-Path $kindProject 'requests'),(Join-Path $kindProject 'outputs') | Out-Null
      Write-Host "Generating $set $kind $name at ${w}x${h}"
      $ok=$false
      for($attempt=1;$attempt -le 3;$attempt++){
        & $py $make --project $kindProject --name $name --brief $brief --reference $ref --width $w --height $h --seed (12000 + $i*2 + $(if($kind -eq 'mobile'){1}else{0}) + $(if($set -eq 'shiva'){100}else{0}) + $attempt)
        if($LASTEXITCODE -eq 0){$ok=$true;break}
        Write-Warning "Retry $attempt for $set $kind $name"
      }
      if(-not $ok){throw "Generation failed for $set $kind $name"}
      Get-ChildItem (Join-Path $kindProject 'outputs') -File | Move-Item -Destination $out -Force
    }
  }
}

