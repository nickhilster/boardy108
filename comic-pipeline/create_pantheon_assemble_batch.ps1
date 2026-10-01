$ErrorActionPreference = 'Stop'
$root = 'D:/DEV/Legion-PanelForge/outputs/pantheon-assemble-2026-09-26'
$briefDir = Join-Path $root 'briefs'
$desktopDir = Join-Path $root 'desktop'
$mobileDir = Join-Path $root 'mobile'
$requestDir = Join-Path $root 'requests'
New-Item -ItemType Directory -Force -Path $briefDir,$desktopDir,$mobileDir,$requestDir | Out-Null

# Ten deities across ten distinct mythological/religious traditions, each
# reimagined as a hero "assembling" for battle. Deliberately excludes
# monotheistic Abrahamic figures (no Allah, Muhammad, YHWH, or Jesus) since
# visual depiction of those is a real sensitivity/prohibition for large
# parts of their practicing communities. Every tradition below has a long,
# unbroken history of artistic reinterpretation of its deities.
$items = @(
  @('zeus-thunder-general', 'A commanding Greek-thunder-god-inspired warrior answering a call to battle, crackling thunderbolt spear raised overhead, storm-cloud cape whipping behind him, chiseled bronze-and-gold armor, low-angle heroic pose against a lightning-split sky, epic modern superhero energy, no text.'),
  @('odin-allfather-vanguard', 'A weathered one-eyed Norse-Allfather-inspired warrior striding forward to assemble, rune-carved spear crackling with pale light, two ravens launching from his shoulders, layered iron-and-fur battle armor, dramatic cinematic backlight through fog, no text.'),
  @('anubis-dusk-sentinel', 'A jackal-headed Egyptian-guardian-inspired warrior rising into a dynamic ready stance, golden ceremonial scales transformed into a battle-worn chest plate, an ankh-bladed staff crossed at the chest, obsidian-and-gold color palette, dramatic desert-dusk rim light, no text.'),
  @('amaterasu-solar-vanguard', 'A radiant Japanese-sun-goddess-inspired warrior stepping forward with a mirrored shield catching the light, flowing white-and-vermilion robes reshaped into a dynamic cape, sunburst aura blazing behind her, serene but powerful expression, cinematic golden light, no text.'),
  @('durga-ninefold-blade', 'A fierce multi-armed Hindu-warrior-goddess-inspired figure landing mid-charge atop a great tiger, each hand gripping a different gleaming weapon, crimson-and-gold ceremonial armor reforged for battle, swirling marigold-colored energy trailing behind her, dramatic low-angle heroic composition, no text.'),
  @('quetzalcoatl-sky-serpent', 'A winged Mesoamerican-feathered-serpent-inspired warrior descending in a burst of emerald feathers, obsidian-and-jade plated armor, a serrated wind-blade staff held ready, swirling turquoise energy coiling around the limbs, dramatic jungle-temple backdrop at dusk, no text.'),
  @('oya-storm-twin-blades', 'A Yoruba-storm-goddess-inspired warrior erupting from a spinning wall of wind, twin curved machetes crossed and crackling with static, layered copper-and-indigo cloth armor whipping like flame, fierce commanding stance, dramatic storm-lit composition, no text.'),
  @('anansi-web-strategist', 'An agile West-African-trickster-spider-inspired warrior perched mid-leap on taut silken lines, segmented obsidian-and-bronze armor with subtle spider-leg silhouettes at the back, a sly confident grin, dynamic acrobatic hero pose against a moonlit web-strung sky, no text.'),
  @('perun-storm-axe', 'A towering Slavic-thunder-god-inspired warrior planting a lightning-wreathed battle-axe into cracked earth, oak-and-iron armor scorched at the edges, storm clouds gathering behind broad shoulders, fierce weathered expression, dramatic monochrome-storm lighting with electric-blue accents, no text.'),
  @('pele-molten-crown', 'A commanding Hawaiian-volcano-goddess-inspired warrior striding across cracked obsidian ground, molten-lava hair flowing like a cape, ember-and-black volcanic-glass armor glowing at the seams, one hand igniting with fresh lava, dramatic eruption-lit composition, no text.')
)
for ($i = 0; $i -lt $items.Count; $i++) {
  Set-Content -LiteralPath (Join-Path $briefDir ('{0:d2}-{1}.txt' -f ($i + 1), $items[$i][0])) -Value $items[$i][1] -Encoding UTF8
}

$ref = 'E:/AI/ComicPipeline/projects/mecha-what-pipeline-test/outputs/panel-07-style-bendaydots_00001_.png'
$cfg = Get-Content 'D:/DEV/Legion-PanelForge/comic-pipeline/pipeline.config.json' | ConvertFrom-Json
$py = Join-Path $cfg.comfy_root '.venv/Scripts/python.exe'
$make = 'D:/DEV/Legion-PanelForge/comic-pipeline/make_panel.py'

for ($i = 0; $i -lt $items.Count; $i++) {
  $name = '{0:d2}-{1}' -f ($i + 1), $items[$i][0]
  $brief = Join-Path $briefDir ($name + '.txt')
  foreach ($target in @(@('desktop', 1536, 864, $desktopDir), @('mobile', 864, 1536, $mobileDir))) {
    $kind, $w, $h, $out = $target
    $kindProject = Join-Path $root $kind
    New-Item -ItemType Directory -Force -Path (Join-Path $kindProject 'requests'), (Join-Path $kindProject 'outputs') | Out-Null
    if (Get-ChildItem $out -Filter "$name`_*.png" -File -ErrorAction SilentlyContinue) { Write-Host "Skipping existing $kind $name"; continue }
    Write-Host "Generating $kind $name at ${w}x${h}"
    $ok = $false
    for ($attempt = 1; $attempt -le 3; $attempt++) {
      & $py $make --project $kindProject --name $name --brief $brief --reference $ref --width $w --height $h --seed (13000 + $i * 2 + $(if ($kind -eq 'mobile') { 1 } else { 0 }) + $attempt)
      if ($LASTEXITCODE -eq 0) { $ok = $true; break }
      Write-Warning "Retry $attempt for $kind $name"
    }
    if (-not $ok) { throw "Generation failed for $kind $name" }
    Get-ChildItem (Join-Path $kindProject 'outputs') -File | Move-Item -Destination $out -Force
  }
}
