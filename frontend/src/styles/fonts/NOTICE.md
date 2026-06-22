# Vendored fonts

These `.woff2` files are **IBM Plex Sans** and **IBM Plex Mono**, vendored
locally so the console runs fully offline (no CDN at runtime).

- Family: IBM Plex Sans (weights 400/500/600/700), IBM Plex Mono (weight 400)
- Subset: Latin
- License: SIL Open Font License 1.1 (OFL-1.1) — https://opensource.org/license/ofl-1-1
- Source: `@fontsource/ibm-plex-sans` and `@fontsource/ibm-plex-mono` (v5.2.6)
- Upstream project: https://github.com/IBM/plex

To update: re-download the matching `latin-<weight>-normal.woff2` files from the
fontsource package at the desired version and replace these files. Weights are
declared in `../fonts.css`.
