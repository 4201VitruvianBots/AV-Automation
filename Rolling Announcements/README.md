# Rolling Announcements

Loops a Google Slides deck in an OBS Browser Source, with per-slide timings.

- **Link builder:** https://4201vitruvianbots.github.io/AV-Automation/
- **Display page:** `announcements.html` - the link builder makes its URL for you.

## Using it

1. Share the deck as **Anyone with the link can view**.
2. Open the link builder, paste the deck link, and set the default seconds, fade and background.
   Set custom seconds on any slide that needs it.
3. Copy the OBS link and paste it as the URL of a **Browser** source (width/height = your canvas, e.g. 1920 x 1080).

All settings live in that URL - nothing is stored anywhere else. To change them, paste the OBS link
back into the link builder's deck box, adjust, and copy the new link into OBS.

## Behaviour

- Slides are downloaded from Google once, when the Browser Source loads. Refresh the source in OBS
  (or change its URL) to pick up edits to the deck.
- When the scene goes live, the loop restarts from slide 1.
- How long each slide stays up: custom timing from the link builder, else a timing in the slide's speaker
  notes (`12s`, `time: 12`, `1.5 min`), else the default.
- Slides marked **Skip slide** in Google Slides are left out.
- Slides are shown as still images: videos, GIFs and animations don't play.
- Custom timings are matched to slides by their text and pictures, so they survive reordering but reset
  if a slide's content changes.

## URL options (announcements.html)

| Option    | Example        | Meaning                                    |
|-----------|----------------|--------------------------------------------|
| `deck`    | `1AH8Zm...`    | Google Slides deck ID (or full link)       |
| `default` | `8`            | Seconds per slide without its own timing (5) |
| `fade`    | `0`            | Cross-fade in milliseconds, 0 = cut (600)  |
| `bg`      | `transparent`  | Colour around the slide, hex or `transparent` (000000) |
| `t`       | `ab12cd:12,...`| Custom seconds per slide (made by the link builder) |

## Local testing

Google won't send the slides to a page opened straight from disk (`file://`), so serve the folder over http:

```bash
python -m http.server 8000
```

then open http://localhost:8000/.
