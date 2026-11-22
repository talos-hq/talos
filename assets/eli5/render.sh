#!/bin/sh
# Render the README's "Explain like I'm 5" install GIFs.
# Needs manim, ffmpeg and gifsicle (NixOS: nix shell nixpkgs#manim nixpkgs#gifsicle).
set -eu
cd "$(dirname "$0")"
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
render() { # scene gif
  manim render -q h --fps 12 -r 640,360 --media_dir "$out" eli5_install.py "$1" >/dev/null
  mp4=$(find "$out" -name "$1.mp4")
  ffmpeg -loglevel error -y -i "$mp4" \
    -vf "split[a][b];[a]palettegen=max_colors=32:stats_mode=diff[p];[b][p]paletteuse=dither=none" \
    -loop 0 "$out/$2"
  gifsicle -O3 --lossy=40 "$out/$2" -o "$2"
  ls -l "$2"
}
render WindowsInstall install-windows.gif
render MacLinuxInstall install-macos-linux.gif
