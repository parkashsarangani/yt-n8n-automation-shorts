# Channel logo (corner watermark)

Drop the gold Behind the Glance mark (two facing profiles forming an eye) here as **`logo.png`**. Whenever it exists,
every rendered Short gets it burned in as a small watermark in the top-right
corner (clear of the top-center wordmark/handle and the bottom caption safe
zone). Without it, this is a clean no-op - no overlay, no error.

It gets baked into the image by the Dockerfile's `COPY`, so committing the
file here is enough - no volume needed (same as `music/signature.mp3`).

## File requirements

- **PNG with a transparent background** - a solid-background image will show
  as an ugly box in the corner instead of a clean badge.
- Any resolution is fine - it's auto-scaled to ~100px wide (height follows
  proportionally) at render time.
- Roughly square/circular logos (like a lightbulb mark) read best at that
  small a size; a wide horizontal lockup will end up tiny and illegible.

## Text identity (already live, no file needed)

The channel **name** ("Behind the Glance") and **handle**
("@behindtheglance") are burned in as text automatically - no asset
required. Both are configurable via env vars (`CHANNEL_WORDMARK`,
`CHANNEL_HANDLE`) without a code change.
