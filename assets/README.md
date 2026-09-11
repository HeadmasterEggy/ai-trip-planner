# Assets

## `ai-trip-planner-logo.svg`

The logo as supplied. It is worth knowing what is inside it before using it
directly: it is **not vector art**. The file is one `<image>` element wrapping a
base64 PNG — 1279×1230 with a transparent background — which makes it 837KB.

## `ai-trip-planner-logo.png`

The 128×123 render the app actually displays, generated from the embedded PNG
above. `st.logo` inlines the image bytes into **every rerun**, and the mark is
drawn at 32px, so the full-size file costs about 1.5MB per rerun against 1.8KB
for this one. Nothing is lost by the downscale: the source is a raster image, not
a vector, and 128px still covers a 4× device pixel ratio at display size.

To regenerate it after replacing the SVG (Pillow ships with Streamlit):

```bash
python - <<'PY'
import base64, io, re, pathlib
from PIL import Image

src = pathlib.Path("assets/ai-trip-planner-logo.svg").read_text()
data = re.search(r'xlink:href="data:image/png;base64,([^"]+)"', src).group(1)
logo = Image.open(io.BytesIO(base64.b64decode(data))).convert("RGBA")
logo.thumbnail((128, 128), Image.LANCZOS)
logo.save("assets/ai-trip-planner-logo.png", optimize=True)
PY
```

`tests/test_theme.py` checks that the displayed file stays small and that both
files are present, so a future swap cannot quietly reintroduce the cost.
