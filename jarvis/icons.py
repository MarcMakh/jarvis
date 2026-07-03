"""Extract Windows shortcut / executable icons as PNG (cached on disk), so the
ecosystem map can show a game's real icon instead of a plain dot.

Uses a small PowerShell helper (System.Drawing) that resolves .lnk targets and
.url IconFile entries, then rasterises the associated icon to PNG.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .util import run, which

_DATA = Path(__file__).resolve().parent.parent / "data"
_CACHE = _DATA / "icon_cache"
_SCRIPT = _CACHE / "_extract_icon.ps1"

_SCRIPT_TEXT = r"""
param([string]$Src,[string]$Out)
Add-Type -AssemblyName System.Drawing
try {
  $p = $Src
  $ext = [System.IO.Path]::GetExtension($Src)
  if ($ext -ieq '.lnk') {
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut($Src)
    if ($lnk.TargetPath -and (Test-Path -LiteralPath $lnk.TargetPath)) { $p = $lnk.TargetPath }
    $il = $lnk.IconLocation
    if ($il) { $ic = ($il -split ',')[0]; if ($ic -and (Test-Path -LiteralPath $ic)) { $p = $ic } }
  } elseif ($ext -ieq '.url') {
    $line = (Get-Content -LiteralPath $Src -ErrorAction SilentlyContinue | Where-Object { $_ -match '^IconFile=' } | Select-Object -First 1)
    if ($line) { $ip = $line -replace '^IconFile=',''; if ($ip -and (Test-Path -LiteralPath $ip)) { $p = $ip } }
  }
  if ([System.IO.Path]::GetExtension($p) -ieq '.ico') {
    $icon = New-Object System.Drawing.Icon($p)
  } else {
    $icon = [System.Drawing.Icon]::ExtractAssociatedIcon($p)
  }
  $bmp = $icon.ToBitmap()
  $bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
} catch { }
""".lstrip()


def _cache_path(path: str) -> Path:
    return _CACHE / (hashlib.sha1(path.encode("utf-8")).hexdigest() + ".png")


def icon_png(path: str) -> bytes | None:
    """Return PNG bytes for a shortcut/exe icon, extracting + caching on first use."""
    p = Path(path)
    if not p.exists():
        return None
    out = _cache_path(path)
    if out.exists() and out.stat().st_size > 0:
        return out.read_bytes()

    _CACHE.mkdir(parents=True, exist_ok=True)
    if not _SCRIPT.exists():
        _SCRIPT.write_text(_SCRIPT_TEXT, encoding="utf-8")

    ps = which("powershell") or "powershell"
    run(
        [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(_SCRIPT), "-Src", str(p), "-Out", str(out)],
        timeout=20,
    )
    if out.exists() and out.stat().st_size > 0:
        return out.read_bytes()
    return None
