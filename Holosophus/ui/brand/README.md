# AI Researcher favicon

`favicon-source.png` is the selected original image generated with the built-in
OpenAI image generation tool for this project. No input image, external logo,
stock icon, or font was supplied. The first textured candidate was discarded.
The prompt below produced the selected image. This records provenance, not a
trademark clearance or an exclusive-rights determination.

The source is retained here outside Vite's public assets. Only its small exports
are served: `public/favicon.ico` (16/32/48 px), `public/ai-researcher-icon-32.png`,
`public/ai-researcher-icon-192.png`, and `public/apple-touch-icon.png` (180 px).
They are connected in `index.html`. The previous SVG and in-app Layers3 mark
remain unchanged.

## Generation prompt

> Create one original flat favicon for AI Researcher. Square 1:1 image. The ENTIRE canvas is a perfectly uniform opaque flat teal color #0f766e, right up to every edge and corner. Center a large bold WHITE geometric A-like arch: two thick smooth diagonal stems meeting at a rounded apex, with a separate short thick horizontal crossbar. Add one medium white round dot above and to the right of the arch, with generous spacing from the apex. The white mark occupies about 75 percent of the square. This is a tiny browser-tab icon, legible at 16 pixels. Only TWO solid colors total: uniform teal and pure white, with antialiasing only at symbol edges. Perfectly clean 2D vector-style silhouette. NO gradients, NO texture, NO shadows, NO dimensional effects, NO grunge, NO pattern, NO holes or cutouts in the teal background, NO transparency, NO border, NO typography, NO wordmark, NO mockup, NO presentation sheet. Invent a simple mark from scratch without referencing existing brands.

Tool option: `transparent_background=false`.

## Export

Only resizing, encoding and metadata removal were applied with ImageMagick:

```bash
convert ui/brand/favicon-source.png -resize 32x32 -strip ui/public/ai-researcher-icon-32.png
convert ui/brand/favicon-source.png -resize 192x192 -strip ui/public/ai-researcher-icon-192.png
convert ui/brand/favicon-source.png -resize 180x180 -strip ui/public/apple-touch-icon.png
convert ui/brand/favicon-source.png -define icon:auto-resize=48,32,16 -strip ui/public/favicon.ico
```
